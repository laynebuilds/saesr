"""Saesr durable actors v2: one Camera actor per camera, one Site actor per site.

    backend/app/actors.py bridge
        |  Camera(cam).message(ActorMessage)   detection | relay_update | action_update | approve | close | heartbeat
        v
    Camera "cam-01"  persisted CameraCore: incidents, counters, outbox of relay calls, verification deadlines
        |  broadcast CameraState to camera sockets
        |  Site("site-demo").update_camera(CameraState)
        v
    Site "site-demo" persisted per-camera states, broadcast SiteState to site sockets

Every actor instance runs ONE CALL AT A TIME (no method is @interleave), so the state machine
below never races with itself: a relay update, an approval and a detection for the same camera
apply one after another, each seeing the previous commit. Persisted fields commit atomically
after each successful call and survive restarts of the actors server.

The whole camera state machine lives in plain functions over CameraCore (handle, tick, recover,
mark_delivered, to_state) so it is unit tested without the runtime (durable/tests/test_logic.py).
The actor methods are thin wrappers that add the clock, alarms, broadcasts and the Site roll-up.

The contract models below mirror backend/app/schema.py (shared contract v2). They are copied,
not imported, because the actors server bundles only this project. tests/test_contract.py
fails if the two drift apart.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

from durable_actors import Actor, ActorSocket, JsonValue, persisted

# ---------------------------------------------------------------------------
# Contract v2 (mirror of backend/app/schema.py; keep identical. dict[str, Any] is spelled
# dict[str, JsonValue] here because actor contracts reject Any; the JSON is the same.)
# ---------------------------------------------------------------------------

SITE_ID = "site-demo"

EventType = Literal["unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves", "compliant_observation"]
IncidentStatus = Literal[
    "verifying", "confirmed", "dismissed", "unverified", "awaiting_approval",
    "actioned", "resolved", "recurring", "closed",
]
RelayStep = Literal["triage", "coach", "fixer", "verifier", "reporter"]
ActionKind = Literal["assign_coaching", "open_corrective_record", "flag_batch_hold", "notify_floor_display", "slow_machine"]
ActionStatus = Literal["pending", "approved", "done", "failed", "awaiting_approval", "rejected"]
ACTION_TIER: dict[str, int] = {
    "assign_coaching": 0, "open_corrective_record": 0, "flag_batch_hold": 0,
    "notify_floor_display": 1, "slow_machine": 2,
}
CameraStatusT = Literal["online", "offline", "recovering"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Point(BaseModel):
    x: float
    y: float


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    site_id: str = SITE_ID
    camera_id: str
    type: EventType
    confidence: float = Field(ge=0, le=1)
    timestamp: str = Field(default_factory=now_iso)
    zone_id: str
    bbox: Optional[tuple[float, float, float, float]] = None
    centroid: Optional[Point] = None
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    keyframes: list[str] = Field(default_factory=list)
    clip_url: Optional[str] = None


class TraceEntry(BaseModel):
    agent: str
    model: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    output_summary: Optional[str] = None
    error: Optional[str] = None


class Action(BaseModel):
    action_id: str
    incident_id: str
    kind: ActionKind
    status: ActionStatus = "pending"
    params: dict[str, JsonValue] = Field(default_factory=dict)
    approved_by: Optional[str] = None
    executed_at: Optional[str] = None


class Incident(BaseModel):
    incident_id: str
    camera_id: str
    type: EventType
    zone_id: str
    status: IncidentStatus = "verifying"
    severity: Optional[int] = Field(default=None, ge=1, le=5)
    event_ids: list[str] = Field(default_factory=list)
    opened_at: str
    updated_at: str
    relay: dict[str, JsonValue] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    keyframes: list[str] = Field(default_factory=list)
    clip_url: Optional[str] = None
    verify_until: Optional[str] = None
    low_confidence: bool = False
    closed_at: Optional[str] = None


class DetectionMsg(BaseModel):
    kind: Literal["detection"] = "detection"
    event: Event


class RelayUpdateMsg(BaseModel):
    kind: Literal["relay_update"] = "relay_update"
    incident_id: str
    step: RelayStep
    status: str
    output: dict[str, JsonValue] = Field(default_factory=dict)
    trace: Optional[TraceEntry] = None


class ActionUpdateMsg(BaseModel):
    kind: Literal["action_update"] = "action_update"
    incident_id: str
    action: Action


class ApproveMsg(BaseModel):
    kind: Literal["approve"] = "approve"
    incident_id: str
    action_id: str
    user: str


class CloseMsg(BaseModel):
    kind: Literal["close"] = "close"
    incident_id: str
    reason: Optional[str] = None


class HeartbeatMsg(BaseModel):
    kind: Literal["heartbeat"] = "heartbeat"
    fps: Optional[float] = None


ActorMessage = Annotated[
    Union[DetectionMsg, RelayUpdateMsg, ActionUpdateMsg, ApproveMsg, CloseMsg, HeartbeatMsg],
    Field(discriminator="kind"),
]
MESSAGE_ADAPTER: TypeAdapter[Any] = TypeAdapter(ActorMessage)


class RelayStart(BaseModel):
    """Actor -> POST {RELAY_URL}/relay/start when an incident opens or reaches a new step."""
    incident_id: str
    camera_id: str
    step: RelayStep = "triage"
    event: Event
    keyframes: list[str] = Field(default_factory=list)
    clip_url: Optional[str] = None
    action: Optional[Action] = None


class CameraState(BaseModel):
    camera_id: str
    site_id: str = SITE_ID
    status: CameraStatusT = "online"
    last_heartbeat: Optional[str] = None
    version: int = 0
    recovered_from_version: Optional[int] = None
    compliance_score: float = 100.0
    counters: dict[str, int] = Field(default_factory=dict)
    false_positives_filtered: int = 0
    autonomous_fixes: int = 0
    incidents: list[Incident] = Field(default_factory=list)


class SiteState(BaseModel):
    site_id: str = SITE_ID
    site_score: float = 100.0
    active_incidents: int = 0
    autonomous_fixes: int = 0
    cameras: list[CameraState] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Actor-internal models
# ---------------------------------------------------------------------------


def _env_ms(name: str, default_s: float) -> int:
    try:
        return int(float(os.environ.get(name, default_s)) * 1000)
    except ValueError:
        return int(default_s * 1000)


JOIN_WINDOW_MS = 10_000  # a violation within 10 s of an open incident's last event (same type+zone) joins it
VERIFY_WINDOW_MS = _env_ms("VERIFY_WINDOW_S", 20)  # verification window after the last action finishes
OFFLINE_AFTER_MS = _env_ms("CAMERA_OFFLINE_S", 15)  # no heartbeat or detection for this long = offline
RECOVERING_MS = _env_ms("RECOVERING_S", 3)  # how long a reloaded camera shows "recovering"
SCORE_RECOVERY_MS = 10 * 60_000  # a resolved incident's penalty fades to zero over 10 minutes
INCIDENTS_MAX = 50
SEEN_IDS_MAX = 1000
DELIVERED_MAX = 500

STEPS: tuple[str, ...] = ("triage", "coach", "fixer", "verifier", "reporter")
OPEN_STATUSES = frozenset({"verifying", "confirmed", "unverified", "awaiting_approval", "recurring"})
ACTIVE_STATUSES = OPEN_STATUSES | {"actioned"}
FINISHED_ACTIONS = frozenset({"done", "failed", "rejected"})

# Changes every time the actor host process starts. A camera whose persisted boot_id differs was
# reloaded from storage by a new process, i.e. the actors server restarted: that is a recovery.
BOOT_ID = uuid.uuid4().hex


class OutboxItem(BaseModel):
    """One relay call the backend dispatcher must POST to {RELAY_URL}/relay/start.

    `id` is stable (incident:step[:rN] or incident:exec:action) and doubles as the
    X-Idempotency-Key header, so the relay can drop a resend it already accepted.
    """

    id: str
    created_at: str
    start: RelayStart


class CameraCore(BaseModel):
    """Everything a Camera actor persists. CameraState is derived from it."""

    camera_id: str
    site_id: str = SITE_ID
    status: CameraStatusT = "online"
    last_heartbeat: Optional[str] = None
    version: int = 0
    recovered_from_version: Optional[int] = None
    counters: dict[str, int] = Field(default_factory=dict)
    false_positives_filtered: int = 0
    autonomous_fixes: int = 0
    incidents: list[Incident] = Field(default_factory=list)  # oldest first
    seen_event_ids: list[str] = Field(default_factory=list)  # bounded dedupe memory
    outbox: list[OutboxItem] = Field(default_factory=list)  # pending relay calls, oldest first
    delivered_ids: list[str] = Field(default_factory=list)  # outbox ids the relay accepted (bounded)
    incident_events: dict[str, Event] = Field(default_factory=dict)  # event sent with each relay call
    last_event_ms: dict[str, int] = Field(default_factory=dict)  # per incident, for the 10 s join window
    escalations: dict[str, int] = Field(default_factory=dict)  # per incident, recurrence count
    last_contact_ms: int = 0
    recovering_until_ms: Optional[int] = None
    boot_id: str = ""


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ms(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _remember(items: list[str], value: str, limit: int) -> None:
    items.append(value)
    if len(items) > limit:
        del items[: len(items) - limit]


def find_incident(core: CameraCore, incident_id: str) -> Optional[Incident]:
    for inc in core.incidents:
        if inc.incident_id == incident_id:
            return inc
    return None


def _trace(inc: Incident, agent: str, summary: str, now: int) -> None:
    t = iso_ms(now)
    inc.trace.append(TraceEntry(agent=agent, started_at=t, ended_at=t, output_summary=summary))


def outbox_id(core: CameraCore, inc: Incident, step: str, action: Optional[Action] = None) -> str:
    if action is not None:
        return f"{inc.incident_id}:exec:{action.action_id}"
    rnd = core.escalations.get(inc.incident_id, 0)
    return f"{inc.incident_id}:{step}" + (f":r{rnd}" if rnd else "")


def enqueue(core: CameraCore, inc: Incident, step: str, now: int, action: Optional[Action] = None) -> bool:
    """Queue a relay call. Idempotent: an id already pending or already delivered is not queued again."""
    oid = outbox_id(core, inc, step, action)
    if oid in core.delivered_ids or any(o.id == oid for o in core.outbox):
        return False
    event = core.incident_events.get(inc.incident_id)
    if event is None:  # should not happen; keep the relay call valid anyway
        event = Event(camera_id=core.camera_id, type=inc.type, confidence=1.0, zone_id=inc.zone_id)
    start = RelayStart(
        incident_id=inc.incident_id, camera_id=core.camera_id, step=step,  # type: ignore[arg-type]
        event=event, keyframes=list(inc.keyframes), clip_url=inc.clip_url, action=action,
    )
    core.outbox.append(OutboxItem(id=oid, created_at=iso_ms(now), start=start))
    return True


def mark_delivered(core: CameraCore, oid: str) -> bool:
    """The relay accepted outbox item `oid` (2xx). Removes it; unknown ids are a no-op."""
    before = len(core.outbox)
    core.outbox = [o for o in core.outbox if o.id != oid]
    if len(core.outbox) == before:
        return False
    if oid not in core.delivered_ids:
        _remember(core.delivered_ids, oid, DELIVERED_MAX)
    return True


def _drop_outbox(core: CameraCore, incident_id: str) -> None:
    core.outbox = [o for o in core.outbox if o.start.incident_id != incident_id]


def _prune(core: CameraCore) -> None:
    """Keep the newest INCIDENTS_MAX incidents, dropping finished ones first."""
    while len(core.incidents) > INCIDENTS_MAX:
        victim = next((i for i in core.incidents if i.status not in ACTIVE_STATUSES), core.incidents[0])
        core.incidents.remove(victim)
        for d in (core.incident_events, core.last_event_ms, core.escalations):
            d.pop(victim.incident_id, None)
        _drop_outbox(core, victim.incident_id)


def _verify_ms(inc: Incident) -> Optional[int]:
    if inc.status != "actioned" or not inc.verify_until:
        return None
    return parse_ms(inc.verify_until)


def check_actioned(inc: Incident, now: int) -> None:
    """confirmed/unverified/awaiting_approval -> actioned once the fixer step reported and every
    action is finished (done, failed or rejected). Opens the verification window."""
    if inc.status not in ("confirmed", "unverified", "awaiting_approval"):
        return
    if "fixer" not in inc.relay or not inc.actions:
        return
    if any(a.status not in FINISHED_ACTIONS for a in inc.actions):
        return
    inc.status = "actioned"
    inc.verify_until = iso_ms(now + VERIFY_WINDOW_MS)
    _trace(inc, "verification", f"window open for {VERIFY_WINDOW_MS // 1000}s", now)


def on_detection(core: CameraCore, ev: Event, now: int) -> None:
    if ev.event_id in core.seen_event_ids:
        return  # same event twice is a no-op
    _remember(core.seen_event_ids, ev.event_id, SEEN_IDS_MAX)
    core.counters[ev.type] = core.counters.get(ev.type, 0) + 1
    core.last_contact_ms = now
    if core.status == "offline":
        core.status = "online"

    if ev.type == "compliant_observation":
        # Feeds verification only: resolves every incident in this zone whose window is open.
        for inc in core.incidents:
            if _verify_ms(inc) is not None and inc.zone_id == ev.zone_id:
                inc.status = "resolved"
                inc.verify_until = None
                inc.low_confidence = False
                _trace(inc, "verification", f"compliant observation {ev.event_id} in {ev.zone_id}", now)
        return

    # A repeat of a fixed violation inside its verification window: recurring, escalate.
    for inc in reversed(core.incidents):
        if _verify_ms(inc) is not None and inc.type == ev.type and inc.zone_id == ev.zone_id:
            inc.status = "recurring"
            inc.verify_until = None
            inc.event_ids.append(ev.event_id)
            core.last_event_ms[inc.incident_id] = now
            core.escalations[inc.incident_id] = core.escalations.get(inc.incident_id, 0) + 1
            core.incident_events[inc.incident_id] = ev.model_copy(
                update={"metrics": {**ev.metrics, "escalation": "recurring"}}
            )
            _trace(inc, "verification", f"recurring: {ev.type} again in {ev.zone_id} ({ev.event_id}); escalating", now)
            enqueue(core, inc, "fixer", now)
            return

    # Join an open incident of the same type and zone seen in the last 10 s.
    for inc in reversed(core.incidents):
        if (
            inc.status in OPEN_STATUSES
            and inc.type == ev.type
            and inc.zone_id == ev.zone_id
            and now - core.last_event_ms.get(inc.incident_id, 0) <= JOIN_WINDOW_MS
        ):
            inc.event_ids.append(ev.event_id)
            core.last_event_ms[inc.incident_id] = now
            return

    t = iso_ms(now)
    inc = Incident(
        incident_id=f"inc-{ev.event_id}",
        camera_id=core.camera_id,
        type=ev.type,
        zone_id=ev.zone_id,
        event_ids=[ev.event_id],
        opened_at=t,
        updated_at=t,
        keyframes=list(ev.keyframes),
        clip_url=ev.clip_url,
    )
    core.incidents.append(inc)
    core.incident_events[inc.incident_id] = ev
    core.last_event_ms[inc.incident_id] = now
    enqueue(core, inc, "triage", now)
    _prune(core)


def on_relay_update(core: CameraCore, m: RelayUpdateMsg, now: int) -> None:
    inc = find_incident(core, m.incident_id)
    if inc is None or inc.status == "closed":
        return
    if m.trace is not None and m.trace not in inc.trace:
        inc.trace.append(m.trace)
    inc.relay[m.step] = {**m.output, "status": m.status}

    if m.step == "triage":
        sev = m.output.get("severity")
        if isinstance(sev, (int, float)) and not isinstance(sev, bool):
            inc.severity = max(1, min(5, int(sev)))
        if inc.status == "verifying":
            if m.status in ("confirmed", "dismissed", "unverified"):
                inc.status = m.status  # type: ignore[assignment]
            elif m.status == "failed":
                inc.status = "unverified"
            else:
                inc.status = "confirmed"
            if inc.status == "dismissed":
                core.false_positives_filtered += 1
    if inc.status == "dismissed":
        _drop_outbox(core, inc.incident_id)
        return  # the relay stops after a dismissal

    idx = STEPS.index(m.step)
    if idx + 1 < len(STEPS):
        enqueue(core, inc, STEPS[idx + 1], now)
    if m.step == "fixer":
        check_actioned(inc, now)


def on_action_update(core: CameraCore, m: ActionUpdateMsg, now: int) -> None:
    inc = find_incident(core, m.incident_id)
    if inc is None or inc.status == "closed":
        return
    new = m.action.model_copy(update={"incident_id": inc.incident_id})
    tier = ACTION_TIER.get(new.kind, 0)
    pos, existing = next(((i, a) for i, a in enumerate(inc.actions) if a.action_id == new.action_id), (-1, None))
    if existing is not None and existing.status in ("done", "rejected"):
        return  # finished actions are never re-executed or counted twice
    if existing is not None and new.approved_by is None:
        new.approved_by = existing.approved_by
    if tier == 2 and new.status in ("approved", "done") and not (existing is not None and existing.approved_by):
        new.status = "awaiting_approval"  # a gated action cannot complete without a human approve
        new.approved_by = None
    if new.status == "done" and new.executed_at is None:
        new.executed_at = existing.executed_at if existing is not None and existing.executed_at else iso_ms(now)
    if existing is not None and new == existing:
        return
    if existing is None:
        inc.actions.append(new)
    else:
        inc.actions[pos] = new
    if new.status == "done" and (tier <= 1 or new.approved_by):
        core.autonomous_fixes += 1
    if new.status == "awaiting_approval" and inc.status in ("verifying", "confirmed", "unverified"):
        inc.status = "awaiting_approval"
    check_actioned(inc, now)


def on_approve(core: CameraCore, m: ApproveMsg, now: int) -> None:
    inc = find_incident(core, m.incident_id)
    if inc is None or inc.status == "closed":
        return
    for i, a in enumerate(inc.actions):
        if a.action_id == m.action_id:
            if a.status not in ("awaiting_approval", "pending"):
                return  # already approved, done or rejected: no-op
            approved = a.model_copy(update={"status": "approved", "approved_by": m.user})
            inc.actions[i] = approved
            _trace(inc, "approval", f"{a.kind} approved by {m.user}", now)
            enqueue(core, inc, "fixer", now, action=approved)
            return


def on_close(core: CameraCore, m: CloseMsg, now: int) -> None:
    inc = find_incident(core, m.incident_id)
    if inc is None or inc.status == "closed":
        return
    inc.status = "closed"
    inc.closed_at = iso_ms(now)
    inc.verify_until = None
    _trace(inc, "close", m.reason or "closed", now)
    _drop_outbox(core, inc.incident_id)


def on_heartbeat(core: CameraCore, now: int) -> None:
    core.last_heartbeat = iso_ms(now)
    core.last_contact_ms = now
    if core.status == "offline":
        core.status = "online"


def tick(core: CameraCore, now: int) -> None:
    """Time-driven transitions: end of recovering, offline timeout, verification timeouts."""
    if core.status == "recovering":
        if core.recovering_until_ms is None or now >= core.recovering_until_ms:
            core.recovering_until_ms = None
            fresh = core.last_contact_ms and now - core.last_contact_ms < OFFLINE_AFTER_MS
            core.status = "online" if fresh else "offline"
    elif core.status == "online" and core.last_contact_ms and now - core.last_contact_ms >= OFFLINE_AFTER_MS:
        core.status = "offline"
    for inc in core.incidents:
        deadline = _verify_ms(inc)
        if deadline is not None and now >= deadline:
            inc.status = "resolved"
            inc.verify_until = None
            inc.low_confidence = True
            _trace(inc, "verification", "window elapsed with no compliant observation: resolved, low confidence", now)


def recover(core: CameraCore, boot_id: str, now: int) -> None:
    """Called before every invocation. A different boot_id means a new actors process reloaded
    this camera from storage: mark it recovering and remember the version it came back at."""
    if core.boot_id == boot_id:
        return
    previous, core.boot_id = core.boot_id, boot_id
    if previous and core.version > 0:
        core.recovered_from_version = core.version
        core.status = "recovering"
        core.recovering_until_ms = now + RECOVERING_MS


def apply_message(core: CameraCore, msg: Any, now: int) -> None:
    if isinstance(msg, DetectionMsg):
        on_detection(core, msg.event, now)
    elif isinstance(msg, RelayUpdateMsg):
        on_relay_update(core, msg, now)
    elif isinstance(msg, ActionUpdateMsg):
        on_action_update(core, msg, now)
    elif isinstance(msg, ApproveMsg):
        on_approve(core, msg, now)
    elif isinstance(msg, CloseMsg):
        on_close(core, msg, now)
    elif isinstance(msg, HeartbeatMsg):
        on_heartbeat(core, now)


def mutate(core: CameraCore, now: int, fn: Callable[[], None]) -> bool:
    """Run fn; if anything changed, stamp updated_at on changed incidents and bump version once.
    A call that changes nothing (duplicate event, re-sent update) leaves version untouched."""
    before = core.model_dump(mode="json")
    before_incidents = {i["incident_id"]: i for i in before["incidents"]}
    fn()
    after = core.model_dump(mode="json")
    if after == before:
        return False
    stamp = iso_ms(now)
    for inc, dumped in zip(core.incidents, after["incidents"]):
        if before_incidents.get(inc.incident_id) != dumped:
            inc.updated_at = stamp
    core.version += 1
    return True


def handle(core: CameraCore, msg: Any, now: int, boot_id: str = "") -> bool:
    """Apply one ActorMessage (model or dict). Returns True when state changed."""
    if isinstance(msg, dict):
        msg = MESSAGE_ADAPTER.validate_python(msg)

    def run() -> None:
        if boot_id:
            recover(core, boot_id, now)
        tick(core, now)
        apply_message(core, msg, now)

    return mutate(core, now, run)


def advance(core: CameraCore, now: int, boot_id: str = "") -> bool:
    """Recovery + time-driven transitions only (alarms, reads)."""

    def run() -> None:
        if boot_id:
            recover(core, boot_id, now)
        tick(core, now)

    return mutate(core, now, run)


def next_alarm(core: CameraCore) -> Optional[int]:
    times: list[int] = []
    if core.recovering_until_ms is not None:
        times.append(core.recovering_until_ms)
    if core.status != "offline" and core.last_contact_ms:
        times.append(core.last_contact_ms + OFFLINE_AFTER_MS)
    for inc in core.incidents:
        deadline = _verify_ms(inc)
        if deadline is not None:
            times.append(deadline)
    return min(times) if times else None


# Compliance score: 100 minus a penalty per incident, weighted by severity (default 3 until triage):
#   open (verifying, confirmed, unverified, awaiting_approval): 4 x severity
#   recurring: 8 x severity
#   actioned (fix applied, being verified): 2 x severity
#   resolved: 2 x severity, fading linearly to 0 over 10 minutes after it resolved
#   dismissed, closed: 0
# Clamped to 0..100, one decimal. The score climbs back as incidents get fixed and verified.
def compliance_score(incidents: list[Incident], now: int) -> float:
    penalty = 0.0
    for inc in incidents:
        sev = inc.severity or 3
        if inc.status == "recurring":
            penalty += 8 * sev
        elif inc.status in OPEN_STATUSES:
            penalty += 4 * sev
        elif inc.status == "actioned":
            penalty += 2 * sev
        elif inc.status == "resolved":
            age = max(0, now - parse_ms(inc.updated_at))
            if age < SCORE_RECOVERY_MS:
                penalty += 2 * sev * (1 - age / SCORE_RECOVERY_MS)
    return round(max(0.0, min(100.0, 100.0 - penalty)), 1)


def to_state(core: CameraCore, now: int) -> CameraState:
    return CameraState(
        camera_id=core.camera_id,
        site_id=core.site_id,
        status=core.status,
        last_heartbeat=core.last_heartbeat,
        version=core.version,
        recovered_from_version=core.recovered_from_version,
        compliance_score=compliance_score(core.incidents, now),
        counters=dict(core.counters),
        false_positives_filtered=core.false_positives_filtered,
        autonomous_fixes=core.autonomous_fixes,
        incidents=[i.model_copy(deep=True) for i in core.incidents],
    )


def rollup(cameras: list[CameraState]) -> SiteState:
    """site_score = mean camera compliance_score; active_incidents = incidents not dismissed,
    resolved or closed; autonomous_fixes = sum over cameras."""
    cams = sorted(cameras, key=lambda c: c.camera_id)
    score = round(sum(c.compliance_score for c in cams) / len(cams), 1) if cams else 100.0
    active = sum(1 for c in cams for i in c.incidents if i.status in ACTIVE_STATUSES)
    return SiteState(
        site_score=score,
        active_incidents=active,
        autonomous_fixes=sum(c.autonomous_fixes for c in cams),
        cameras=cams,
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------


class Viewer(BaseModel):
    """Socket metadata: who is watching."""

    name: str = "dashboard"


class SocketCommand(BaseModel):
    """What a socket may send: {"type": "refresh"} -> that socket gets the current state again."""

    type: Literal["refresh"] = "refresh"


class MessageResult(BaseModel):
    """Result of Camera.message. changed=False means the message was a no-op (duplicate)."""

    ok: bool = True
    changed: bool
    version: int
    state: CameraState
    outbox: list[OutboxItem]


CameraSocket = ActorSocket[Viewer, CameraState]
SiteSocket = ActorSocket[Viewer, SiteState]


class Camera(Actor[Viewer, SocketCommand, CameraState]):
    """One camera. Owns its incidents, the relay outbox and the verification windows.

    Serialized execution means a detection, a relay update and an approval for this camera never
    interleave. A single durable alarm is always set to the earliest pending deadline (offline
    timeout, end of recovering, verification window), so time-driven transitions happen even if
    nothing else ever calls the actor.
    """

    core: Optional[CameraCore] = persisted(None)

    def message(self, msg: JsonValue) -> MessageResult:
        """Apply one ActorMessage (schema.ActorMessage JSON). Idempotent for duplicates."""
        parsed = MESSAGE_ADAPTER.validate_python(msg)
        core = self._core()
        now = _now_ms()
        changed = handle(core, parsed, now, BOOT_ID)
        state = self._after(core, now, changed)
        return MessageResult(changed=changed, version=core.version, state=state, outbox=list(core.outbox))

    def state(self) -> CameraState:
        core = self._core()
        now = _now_ms()
        changed = advance(core, now, BOOT_ID)
        return self._after(core, now, changed)

    def pending_relay(self) -> list[OutboxItem]:
        """Relay calls not yet accepted by the relay, oldest first."""
        core = self._core()
        now = _now_ms()
        self._after(core, now, advance(core, now, BOOT_ID))
        return list(core.outbox)

    def relay_delivered(self, outbox_id: str) -> bool:
        """The relay answered 2xx for this outbox item. Returns False if it was not pending."""
        core = self._core()
        now = _now_ms()

        def run() -> None:
            recover(core, BOOT_ID, now)
            tick(core, now)
            mark_delivered(core, outbox_id)

        changed = mutate(core, now, run)
        self._after(core, now, changed)
        return changed and all(o.id != outbox_id for o in core.outbox)

    def on_alarm(self) -> None:
        core = self._core()
        now = _now_ms()
        self._after(core, now, advance(core, now, BOOT_ID))

    def on_connect(self, socket: CameraSocket) -> None:
        self.set_websocket_auto_response("ping", "pong")
        core = self._core()
        socket.send(to_state(core, _now_ms()))

    def on_message(self, socket: CameraSocket, message: SocketCommand) -> None:
        socket.send(to_state(self._core(), _now_ms()))

    def _core(self) -> CameraCore:
        if self.core is None:
            self.core = CameraCore(camera_id=self.id)
        return self.core

    def _after(self, core: CameraCore, now: int, changed: bool) -> CameraState:
        nxt = next_alarm(core)
        if nxt is None:
            self.alarm.delete()
        else:
            self.alarm.set(max(nxt, now + 50))
        state = to_state(core, now)
        if changed:
            self.broadcast(state)
            try:
                Site.get(core.site_id).update_camera(state)
            except Exception:
                pass  # the Site catches up on the next change; never fail the camera for it
        return state


class Site(Actor[Viewer, SocketCommand, SiteState]):
    """Roll-up of every camera at one site. Cameras push their full state on every change."""

    cameras: dict[str, CameraState] = persisted(default_factory=dict)
    version: int = persisted(0)

    def update_camera(self, state: CameraState) -> None:
        prev = self.cameras.get(state.camera_id)
        if prev is not None and state.version < prev.version:
            return  # stale, out of order
        self.cameras[state.camera_id] = state
        self.version += 1
        self.broadcast(self._state())

    def state(self) -> SiteState:
        return self._state()

    def forget_camera(self, camera_id: str) -> bool:
        """Drop a camera from the roll-up (test cleanup). It reappears on its next change."""
        if self.cameras.pop(camera_id, None) is None:
            return False
        self.version += 1
        self.broadcast(self._state())
        return True

    def on_connect(self, socket: SiteSocket) -> None:
        self.set_websocket_auto_response("ping", "pong")
        socket.send(self._state())

    def on_message(self, socket: SiteSocket, message: SocketCommand) -> None:
        socket.send(self._state())

    def _state(self) -> SiteState:
        return rollup(list(self.cameras.values()))
