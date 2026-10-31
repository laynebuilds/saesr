"""Starts and keeps the agent layer alive. The API never depends on it.

One bus subscription is opened for the life of the process, so incidents that arrive while
Relay is down queue up (bounded) and are posted after the reconnect. Any failure in a session
(Relay down, network error, a bug) is logged and the session restarts with backoff.
"""
import asyncio
import logging
import time
from typing import Callable, Optional

from .. import bus
from . import activity
from .camera import CameraFleet
from .config import AgentsConfig
from .llm import Reasoner
from .relay_client import RelayClient
from .supervisor import Supervisor

log = logging.getLogger("saesr.agents")

BACKOFF_S = (1, 2, 5, 10, 30)  # the last one repeats
STABLE_S = 30.0  # a session that ran this long resets the backoff


class AgentRuntime:
    def __init__(self, cfg: AgentsConfig, relay_factory: Callable = RelayClient, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.relay_factory = relay_factory
        self.clock = clock
        self.reasoner = Reasoner(cfg.openai_model, cfg.llm_timeout_s)
        self.supervisor = Supervisor(cfg, self.reasoner, clock)
        self.fleet = CameraFleet(cfg)
        self.dashboard = None
        activity.status.update(enabled=True, connected=False, relay_url=cfg.base_url, channel=f"#{cfg.channel}",
                               llm=self.reasoner.enabled, llm_note=self.reasoner.disabled_reason, error=None)

    async def run(self) -> None:
        q = bus.open_subscription()
        attempt = 0
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.session(q)
                    log.warning("agent session ended; restarting")
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    log.warning("agent session failed (%s: %s); retrying", type(err).__name__, err)
                    activity.status["error"] = f"{type(err).__name__}: {err}"
                activity.status["connected"] = False
                self.dashboard = None
                if time.monotonic() - started > STABLE_S:
                    attempt = 0
                await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                attempt += 1
        finally:
            bus.close_subscription(q)

    async def session(self, q: asyncio.Queue) -> None:
        cfg = self.cfg
        relay = self.relay_factory(cfg.base_url, cfg.api_key)
        try:
            sup = await relay.register(cfg.supervisor, persona=(
                "Safety supervisor. Correlates camera incidents across cameras and time, escalates to the "
                "floor manager, posts the shift brief. DM 'status' for a brief, 'ack <event id>' or 'ack all'."
            ))
            await sup.ensure_channel(cfg.channel, topic="Saesr: camera agents report incidents, the supervisor correlates and escalates")
            await relay.ensure_exists(cfg.floor_manager, kind="human", persona="Floor manager on shift. Receives escalations.")
            dash = await relay.register(cfg.dashboard, persona="The Saesr dashboard. Relays acknowledgements from the UI.")
            await dash.ensure_channel(cfg.channel)
            await self.supervisor.attach(sup)
            self.dashboard = dash
            activity.status.update(connected=True, error=None,
                                   agents=sorted({cfg.supervisor, cfg.floor_manager, cfg.dashboard, *self.fleet.agents}))
            log.info("agents online in #%s at %s (llm: %s)", cfg.channel, cfg.base_url,
                     "on" if self.reasoner.enabled else f"off, {self.reasoner.disabled_reason}")
            tasks = [asyncio.create_task(self.supervisor.run()), asyncio.create_task(self.fleet.run(relay, q))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()  # re-raise the failure that ended the session
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await relay.close()

    async def ack(self, event_ids: list[str], by: str = "dashboard") -> Optional[dict]:
        """Acknowledge from the dashboard: the dashboard agent says it on the channel, and the
        supervisor picks it up from Relay like any other ack."""
        if self.dashboard is None:
            return None
        text = f"ack {' '.join(event_ids)}\n(from {by} via the Saesr dashboard)"
        msg = await self.dashboard.post(self.cfg.channel, text)
        activity.record_message(self.cfg.dashboard, f"#{self.cfg.channel}", text, relay_id=msg.get("id"))
        return msg
