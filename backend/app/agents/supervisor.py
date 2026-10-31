"""The Safety Supervisor agent.

It only knows what it reads on Relay: incident posts from camera agents on #safety-floor, acks
from anyone on the channel, and DMs addressed to it. It never reads the backend bus, so the
cameras and the supervisor genuinely talk agent to agent.

From that it correlates across cameras and time (correlator.py) and acts by messaging:

    PATTERN     posted to the channel (a correlation worth knowing, not paging anyone)
    ESCALATION  posted to the channel and DMed to the floor manager handle
    SHIFT BRIEF posted to the channel every BRIEF_EVERY_S while incidents keep coming
    ACK         confirmation in the channel when someone acknowledges incidents

Every decision is also recorded as a structured action in activity.py for the dashboard.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from . import activity
from .config import AgentsConfig
from .correlator import Correlator, Finding
from .llm import Reasoner
from .messages import action_line, parse_ack, parse_incident
from .relay_client import RelayError, RelayUnavailable

log = logging.getLogger("saesr.agents.supervisor")

GEAR = {"no_hardhat": "a hard hat", "no_vest": "a high-visibility vest", "no_hairnet": "a hairnet", "no_gloves": "gloves"}
RECENT_EVENTS_MAX = 50
FINDINGS_FOR_BRIEF_MAX = 10


def recommendation(f: Finding) -> str:
    """Deterministic next step per rule, used when no LLM is configured (or it fails)."""
    where = f.zone or ", ".join(f.cameras)
    if f.rule == "zone_hotspot":
        return f"Send a lead to {where} now and pause the task that keeps triggering incidents until it is reviewed."
    if f.rule == "roaming_violator":
        gear = GEAR.get(f.types[0], "the required gear") if f.types else "the required gear"
        return f"Find the worker without {gear} seen on {', '.join(f.cameras)} and stop them at the next station to gear up."
    if f.rule == "shift_spike":
        return "Call a two minute safety stand-down on the floor; the incident rate is well above normal."
    if f.rule == "unacked_high":
        return f"Check {where} in person and acknowledge once it is handled."
    return "Review the incidents on the dashboard."


def _clock(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%H:%M:%SZ")


class Supervisor:
    def __init__(self, cfg: AgentsConfig, reasoner: Optional[Reasoner] = None, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.name = cfg.supervisor
        self.channel = cfg.channel
        self.clock = clock
        self.reasoner = reasoner
        self.correlator = Correlator(cfg.rules)
        self.agent = None
        self.cursor: Optional[str] = None
        self.primed = False  # cursor set once; a reconnect resumes from it instead of skipping ahead
        self.started = clock()
        self.last_brief = self.started
        self.events_since_brief = 0
        self.recent_events: list[dict] = []
        self.findings_since_brief: list[dict] = []
        self._tasks: set[asyncio.Task] = set()

    # ---- lifecycle

    async def attach(self, agent) -> None:
        """Bind to a (re)connected Relay identity. Correlation state survives reconnects."""
        self.agent = agent
        if not self.primed:
            self.cursor = await agent.latest_id(self.channel)  # start live, do not replay history
            self.primed = True
        await agent.inbound_dms()  # prime DM cursors

    async def run(self) -> None:
        while True:
            await self.step()
            await asyncio.sleep(self.cfg.poll_s)

    async def step(self) -> None:
        """One pass: read the channel, read DMs, run time based rules, maybe post the brief."""
        for msg in await self.agent.channel_messages(self.channel, after=self.cursor):
            self.cursor = msg["id"]
            if msg["from"] != self.name:
                await self.handle(msg)
        for msg in await self.agent.inbound_dms():
            activity.record_message(msg["from"], self.name, msg["text"], relay_id=msg["id"])
            await self.handle(msg, direct=True)
        now = self.clock()
        for finding in self.correlator.tick(now):
            self._spawn(self.act(finding))
        if now - self.last_brief >= self.cfg.rules.brief_every_s:
            if self.events_since_brief or self.correlator.open_high:
                self._spawn(self.brief())
            self.last_brief = now
        self._raise_failed()

    async def drain(self) -> None:
        """Wait for in-flight actions (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._raise_failed()

    # ---- input

    async def handle(self, msg: dict, direct: bool = False) -> None:
        text = msg["text"]
        event = parse_incident(text)
        if event is not None:
            self.events_since_brief += 1
            self.recent_events.append({
                "id": event.id, "camera": event.camera, "type": event.type, "severity": event.severity,
                "zone": event.zone, "timestamp": event.timestamp,
            })
            del self.recent_events[:-RECENT_EVENTS_MAX]
            for finding in self.correlator.observe(event, self.clock()):
                self._spawn(self.act(finding))
            return
        ids = parse_ack(text)
        if ids is not None:
            by = msg["from"]
            via = re.search(r"\(from ([A-Za-z0-9 _.@\-]+) via the Saesr dashboard\)", text)
            if by == self.cfg.dashboard and via:
                by = f"{via.group(1)} (dashboard)"
            await self.acknowledge(ids, by=by, reply_to=msg["from"] if direct else None)
            return
        if direct and text.strip().lower() in ("status", "brief", "shift brief"):
            await self.brief(dm_to=msg["from"])

    # ---- actions

    async def act(self, f: Finding) -> None:
        rec, source = recommendation(f), "rules"
        if f.escalate and self.reasoner and self.reasoner.enabled:
            llm = await self.reasoner.escalation(f.as_dict(), self.recent_events)
            if llm:
                rec, source = llm, "llm"
        kind = "escalate" if f.escalate else "pattern"
        action = {
            "type": kind, "rule": f.rule, "severity": f.severity, "summary": f.summary,
            "recommendation": rec, "source": source, "event_ids": f.event_ids, "zone": f.zone,
            "cameras": f.cameras, "types": f.types,
            "notified": [f"#{self.channel}"] + ([self.cfg.floor_manager] if f.escalate else []),
        }
        label = "ESCALATION" if f.escalate else "PATTERN"
        post = f"{label} {f.severity.upper()} {f.rule} | {f.summary}\nAction: {rec}"
        if f.escalate:
            post += f"\nPaged @{self.cfg.floor_manager}."
        await self._post(post + "\n" + action_line({k: action[k] for k in ("type", "rule", "severity", "event_ids")}))
        if f.escalate:
            first = f.event_ids[0] if f.event_ids else "all"
            dm = (
                f"[{f.severity.upper()}] {f.summary}.\n{rec}\n"
                f"Reply 'ack {first}' (or 'ack all') to acknowledge."
            )
            await self._dm(self.cfg.floor_manager, dm)
        self.findings_since_brief.append({"rule": f.rule, "severity": f.severity, "summary": f.summary})
        del self.findings_since_brief[:-FINDINGS_FOR_BRIEF_MAX]
        activity.record_action(self.name, self.cfg.floor_manager if f.escalate else f"#{self.channel}", post, action)
        log.info("%s %s: %s", kind, f.rule, f.summary)

    async def acknowledge(self, ids: list[str], by: str, reply_to: Optional[str] = None) -> list[str]:
        acked = self.correlator.ack(ids)
        if not acked:
            if reply_to:
                await self._dm(reply_to, "Nothing open to acknowledge with those ids.")
            return []
        open_left = len(self.correlator.open_high)
        text = f"ACK {len(acked)} incident{'s' if len(acked) > 1 else ''} acknowledged by {by}. {open_left} high severity still open."
        action = {"type": "ack", "rule": None, "severity": None, "summary": text, "recommendation": None,
                  "source": "rules", "event_ids": acked, "zone": None, "cameras": [], "types": [],
                  "by": by, "notified": [f"#{self.channel}"]}
        await self._post(text + "\n" + action_line({"type": "ack", "event_ids": acked, "by": by}))
        activity.record_action(self.name, f"#{self.channel}", text, action)
        return acked

    async def brief(self, dm_to: Optional[str] = None) -> None:
        now = self.clock()
        snap = self.correlator.snapshot(now, since=now - self.cfg.rules.brief_every_s)
        text, source = self._brief_text(snap, now), "rules"
        if self.reasoner and self.reasoner.enabled:
            llm = await self.reasoner.brief(snap, self.findings_since_brief)
            if llm:
                text, source = f"SHIFT BRIEF {_clock(now)}\n{llm}", "llm"
        action = {"type": "brief", "rule": None, "severity": None, "summary": text, "recommendation": None,
                  "source": source, "event_ids": [], "zone": snap["top_zones"][0][0] if snap["top_zones"] else None,
                  "cameras": [], "types": [], "snapshot": snap, "notified": [dm_to or f"#{self.channel}"]}
        if dm_to:
            await self._dm(dm_to, text)
        else:
            await self._post(text + "\n" + action_line({"type": "brief", "open_unacked_high": snap["open_unacked_high"]}))
            self.events_since_brief = 0
            self.findings_since_brief = []
        activity.record_action(self.name, dm_to or f"#{self.channel}", text, action)

    def _brief_text(self, s: dict, now: float) -> str:
        sev = s["shift_by_severity"]
        zone = f"{s['top_zones'][0][0]} ({s['top_zones'][0][1]})" if s["top_zones"] else "none"
        kind = f"{s['top_types'][0][0]} ({s['top_types'][0][1]})" if s["top_types"] else "none"
        open_high = s["open_unacked_high"]
        unacked = f"{open_high}, oldest {s['oldest_unacked_s']} s" if open_high else "0"
        findings = ", ".join(f"{k} {v}" for k, v in s["findings"].items()) or "none"
        return (
            f"SHIFT BRIEF {_clock(now)}\n"
            f"{s['shift_total']} incidents this shift ({sev.get('high', 0)} high, {sev.get('medium', 0)} medium, "
            f"{sev.get('low', 0)} low), {s['since_last_brief']} in the last {self.cfg.rules.brief_every_s:g} s.\n"
            f"Worst zone: {zone}. Top issue: {kind}. Unacknowledged high: {unacked}.\n"
            f"Patterns so far: {findings}."
        )

    # ---- plumbing

    async def _post(self, text: str) -> None:
        try:
            await self.agent.post(self.channel, text)
        except RelayUnavailable:
            raise
        except RelayError as err:
            log.warning("supervisor post failed: %s", err)

    async def _dm(self, to: str, text: str) -> None:
        try:
            await self.agent.dm(to, text)
        except RelayUnavailable:
            raise
        except RelayError as err:
            log.warning("supervisor DM to %s failed: %s", to, err)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._remember_failure)

    _failure: Optional[BaseException] = None

    def _remember_failure(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        err = task.exception()
        if isinstance(err, RelayUnavailable):
            self._failure = err
        elif err is not None:
            log.error("supervisor action failed", exc_info=err)

    def _raise_failed(self) -> None:
        err, self._failure = self._failure, None
        if err is not None:
            raise err
