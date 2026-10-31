"""Camera agents: one Relay identity per camera id, each posting its own incidents to the channel.

The fleet reads the shared bus once and routes each event to the agent of its camera. A camera
agent is registered the first time its camera reports, so live mode (one camera) and mock mode
(four cameras) need no roster config.
"""
import asyncio
import logging
from typing import Optional

from . import activity
from .config import AgentsConfig
from .messages import incident_text
from .relay_client import RelayError, RelayUnavailable

log = logging.getLogger("saesr.agents.camera")

CAMERA_QUEUE_MAX = 256


class CameraAgent:
    def __init__(self, camera: str, agent, channel: str, pending: list) -> None:
        self.camera = camera
        self.agent = agent
        self.channel = channel
        self.pending = pending  # shared with the fleet: events to retry after a reconnect
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=CAMERA_QUEUE_MAX)
        self.posted = 0

    async def report(self, event) -> None:
        text = incident_text(event)
        try:
            msg = await self.agent.post(self.channel, text)
        except RelayUnavailable:
            raise  # the runtime reconnects; the event stays queued on the bus side
        except RelayError as err:
            log.warning("%s could not post %s: %s", self.camera, event.event_id, err)
            return
        self.posted += 1
        activity.record_message(self.agent.name, f"#{self.channel}", text, event_id=event.event_id, relay_id=msg.get("id"))

    async def run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self.report(event)
            except (RelayUnavailable, asyncio.CancelledError):
                # keep the event for the next session; the fleet also keeps whatever is still queued
                self.pending.append(event)
                raise


class CameraFleet:
    """Lives across Relay reconnects; each run() is one connected session."""

    def __init__(self, cfg: AgentsConfig) -> None:
        self.cfg = cfg
        self.relay = None
        self.agents: dict[str, CameraAgent] = {}
        self.pending: list = []
        self._tasks: list[asyncio.Task] = []

    async def agent_for(self, camera: str) -> CameraAgent:
        cam = self.agents.get(camera)
        if cam is None:
            agent = await self.relay.register(
                camera, persona=f"Camera agent for {camera}. Reports every safety incident this camera sees."
            )
            await agent.ensure_channel(self.cfg.channel)
            cam = CameraAgent(camera, agent, self.cfg.channel, self.pending)
            self.agents[camera] = cam
            self._tasks.append(asyncio.create_task(cam.run()))
            activity.status["agents"] = sorted(set(activity.status["agents"]) | {camera})
            log.info("camera agent %s joined #%s", camera, self.cfg.channel)
        return cam

    async def _dispatch(self, event) -> None:
        if event.type == "compliant_observation":
            return  # the old supervisor only reasons about violations
        cam = await self.agent_for(event.camera_id)
        if cam.queue.full():
            cam.queue.get_nowait()  # drop the oldest rather than grow without bound
        cam.queue.put_nowait(event)

    async def run(self, relay, q: asyncio.Queue) -> None:
        """Route bus events to camera agents until cancelled or a camera agent loses Relay."""
        self.relay = relay
        getter: Optional[asyncio.Future] = None
        try:
            retry, self.pending[:] = list(self.pending), []
            for event in retry[-CAMERA_QUEUE_MAX:]:
                await self._dispatch(event)
            while True:
                if getter is None:
                    getter = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait([getter, *self._tasks], return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is not getter and not task.cancelled() and task.exception():
                        raise task.exception()
                if getter in done:
                    event, getter = getter.result(), None
                    await self._dispatch(event)
        finally:
            if getter is not None:
                # never leave a stray q.get() behind: it would swallow the next incident
                if getter.done() and not getter.cancelled():
                    self.pending.append(getter.result())
                getter.cancel()
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            for cam in self.agents.values():
                while not cam.queue.empty():
                    self.pending.append(cam.queue.get_nowait())
            self._tasks.clear()
            self.agents.clear()

