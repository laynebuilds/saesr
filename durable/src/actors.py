"""Saesr durable actors: one Site per warehouse, one Camera per camera feed.

    CV pipeline / mock --> bus --> backend/app/actors.py bridge
                                       |
                                       v
                          Camera("dock-cam-1").record(event)
                          Camera("dock-cam-1").heartbeat(fps)
                                       |
                                       v
                          Site("demo-warehouse").record(event)  --> broadcast snapshot
                                                                      to every dashboard socket

Why actors. Every Camera and Site instance runs ONE CALL AT A TIME (serialized execution;
no method here is @interleave). When ten cameras report incidents in the same millisecond,
their Site.record calls queue at the Site actor and run one after another, each one reading
the state the previous call committed. There is no read-modify-write race, so a counter can
never lose an increment and the incident list can never drop a concurrent append, without a
single lock in this file. Each successful call commits its persisted fields atomically
(a failed call rolls back), so the state survives crashes and restarts of the actors server.

Pure helpers (compute_summary, apply_record, ...) hold the business logic so it can be unit
tested without the runtime; the actor methods are thin wrappers around them.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from durable_actors import Actor, ActorSocket, persisted

DEFAULT_SITE = "demo-warehouse"
INCIDENTS_MAX = 1000  # Site keeps the newest N incidents
SEEN_IDS_MAX = 5000  # idempotency memory; larger than INCIDENTS_MAX so retries of evicted events still dedupe
SNAPSHOT_RECENT = 20  # incidents included in each broadcast snapshot
SNAPSHOT_OPEN_ALERTS = 50  # open alerts included in each broadcast snapshot (count is always exact)
CAMERA_SEEN_IDS_MAX = 200
OFFLINE_AFTER_MS = 15_000  # a camera with no heartbeat or event for this long is marked offline

# Same compliance score as backend/app/main.py /summary. Keep the two in step.
SEVERITY_WEIGHT = {"low": 1, "medium": 3, "high": 5}
SCORE_K = 2.0

EventType = Literal["unsafe_lift", "no_hardhat", "no_vest", "restricted_zone", "no_hairnet", "no_gloves"]
Severity = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Models (the wire contract; mirror of contract/event.schema.json)
# ---------------------------------------------------------------------------


class SaesrEvent(BaseModel):
    """One detected incident, as published by the CV pipeline."""

    id: str
    camera: str
    type: EventType
    severity: Severity
    timestamp: str
    zone: str
    clip_url: str | None = None
    frame_url: str | None = None
    confidence: float | None = None
    bbox: list[float] | None = None
    detail: str | None = None


class Incident(BaseModel):
    """An event as stored by the Site, plus its acknowledgement."""

    event: SaesrEvent
    recorded_at: str
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None


class RecordResult(BaseModel):
    """Result of Site.record. duplicate=True means the event id was already recorded (no change)."""

    event_id: str
    duplicate: bool
    total: int


class CameraInfo(BaseModel):
    """Per-camera status as seen by the Site (updated when a camera goes online or offline)."""

    camera: str
    online: bool
    fps: float | None = None
    last_seen_at: str | None = None


class Summary(BaseModel):
    """Same shape as GET /summary on the backend (minus `mode`)."""

    total: int
    window_hours: float
    by_type: dict[str, int]
    by_zone: dict[str, int]
    by_camera: dict[str, int]
    by_severity: dict[str, int]
    weighted_incidents_per_hour: float
    compliance_score: float
    last_event: SaesrEvent | None = None


class SiteSnapshot(BaseModel):
    """Everything a dashboard needs to render one warehouse."""

    site: str
    version: int = Field(description="Increments on every change; use it to drop out-of-order messages.")
    updated_at: str | None
    total: int = Field(description="Lifetime incidents recorded (not windowed).")
    by_zone: dict[str, int]
    by_camera: dict[str, int]
    by_type: dict[str, int]
    by_severity: dict[str, int]
    compliance_score: float = Field(description="24h compliance score, 0..100.")
    weighted_incidents_per_hour: float
    window_hours: float
    open_alerts_count: int
    open_alerts: list[Incident] = Field(description="Newest unacknowledged incidents, newest first.")
    recent: list[Incident] = Field(description="Newest incidents, newest first.")
    cameras: dict[str, CameraInfo]


class Viewer(BaseModel):
    """Socket metadata: who is watching."""

    name: str = "dashboard"


class SiteCommand(BaseModel):
    """What a dashboard socket may send.

    {"type": "refresh"} -> the socket receives a fresh snapshot.
    {"type": "ack", "event_id": "...", "by": "jas"} -> acknowledge an alert; everyone gets the new snapshot.
    """

    type: Literal["refresh", "ack"]
    event_id: str | None = None
    by: str | None = None


class SiteMessage(BaseModel):
    """What a Site socket receives: always a full snapshot, tagged with why it was sent."""

    type: Literal["snapshot"] = "snapshot"
    reason: Literal["connect", "refresh", "recorded", "acknowledged", "camera"]
    event_id: str | None = None
    snapshot: SiteSnapshot


class CameraStatus(BaseModel):
    """Durable status of one camera."""

    camera: str
    site: str = DEFAULT_SITE
    status: Literal["online", "offline", "unknown"] = "unknown"
    fps: float | None = None
    last_frame_at: str | None = None
    last_event: SaesrEvent | None = None
    events_seen: int = 0


# ---------------------------------------------------------------------------
# Pure logic (unit tested in durable/tests, no runtime needed)
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_summary(incidents: list[Incident], now: datetime, hours: float = 24.0) -> Summary:
    """Compliance summary over the last `hours`, same formula as backend /summary.

    weighted_per_hour = sum(severity weights in window) / window_hours, weights low=1 medium=3 high=5.
    score = clamp(100 - 2 * weighted_per_hour, 0, 100).
    window_hours = span from the oldest event in the window to now, at least 1h, at most `hours`.
    """
    cutoff = now - timedelta(hours=hours)
    window: list[SaesrEvent] = []
    for inc in incidents:
        try:
            if parse_ts(inc.event.timestamp) >= cutoff:
                window.append(inc.event)
        except ValueError:
            continue  # a malformed timestamp never breaks the summary
    if window:
        oldest = min(parse_ts(e.timestamp) for e in window)
        span = max(1.0, (now - oldest).total_seconds() / 3600)
    else:
        span = 1.0
    weighted = sum(SEVERITY_WEIGHT[e.severity] for e in window)
    per_hour = weighted / span
    score = max(0.0, min(100.0, 100.0 - per_hour * SCORE_K))
    return Summary(
        total=len(window),
        window_hours=round(span, 2),
        by_type=dict(Counter(e.type for e in window)),
        by_zone=dict(Counter(e.zone for e in window)),
        by_camera=dict(Counter(e.camera for e in window)),
        by_severity=dict(Counter(e.severity for e in window)),
        weighted_incidents_per_hour=round(per_hour, 2),
        compliance_score=round(score, 1),
        last_event=max(window, key=lambda e: parse_ts(e.timestamp)) if window else None,
    )


def bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def insert_incident(incidents: list[Incident], incident: Incident, limit: int = INCIDENTS_MAX) -> None:
    """Append, then drop the oldest so at most `limit` remain (list is oldest first)."""
    incidents.append(incident)
    overflow = len(incidents) - limit
    if overflow > 0:
        del incidents[:overflow]


def remember_id(seen: list[str], event_id: str, limit: int) -> None:
    seen.append(event_id)
    overflow = len(seen) - limit
    if overflow > 0:
        del seen[:overflow]


def open_alerts(incidents: list[Incident]) -> list[Incident]:
    """Unacknowledged incidents, newest first."""
    return [i for i in reversed(incidents) if i.acknowledged_at is None]


def acknowledge_incident(incidents: list[Incident], event_id: str, by: str, at: str) -> Incident | None:
    """Mark one incident acknowledged. None if unknown or already acknowledged (idempotent)."""
    for inc in reversed(incidents):
        if inc.event.id == event_id:
            if inc.acknowledged_at is not None:
                return None
            inc.acknowledged_by = by
            inc.acknowledged_at = at
            return inc
    return None


# ---------------------------------------------------------------------------
# Site actor
# ---------------------------------------------------------------------------

SiteSocket = ActorSocket[Viewer, SiteMessage]


class Site(Actor[Viewer, SiteCommand, SiteMessage]):
    """One warehouse. Single writer for its incidents, counters, score and open alerts.

    Serialized execution: record, acknowledge and camera_changed never overlap for the same
    Site id. Many Camera actors call Site.record concurrently; the runtime queues those calls
    and applies them one at a time, so no update is ever lost and every broadcast snapshot is
    a consistent state (counters, incidents and open alerts always agree).
    """

    incidents: list[Incident] = persisted(default_factory=list)  # oldest first, newest INCIDENTS_MAX
    seen_ids: list[str] = persisted(default_factory=list)
    total: int = persisted(0)
    by_zone: dict[str, int] = persisted(default_factory=dict)
    by_camera: dict[str, int] = persisted(default_factory=dict)
    by_type: dict[str, int] = persisted(default_factory=dict)
    by_severity: dict[str, int] = persisted(default_factory=dict)
    cameras: dict[str, CameraInfo] = persisted(default_factory=dict)
    version: int = persisted(0)
    updated_at: str | None = persisted(None)

    def record(self, event: SaesrEvent) -> RecordResult:
        """Record one incident. Idempotent by event id: a retry of the same event changes nothing."""
        if event.id in self.seen_ids:
            return RecordResult(event_id=event.id, duplicate=True, total=self.total)
        remember_id(self.seen_ids, event.id, SEEN_IDS_MAX)
        insert_incident(self.incidents, Incident(event=event, recorded_at=now_iso()))
        # Read-modify-write on shared counters. Safe only because calls are serialized.
        self.total += 1
        bump(self.by_zone, event.zone)
        bump(self.by_camera, event.camera)
        bump(self.by_type, event.type)
        bump(self.by_severity, event.severity)
        self._changed("recorded", event.id)
        return RecordResult(event_id=event.id, duplicate=False, total=self.total)

    def acknowledge(self, event_id: str, by: str) -> Incident | None:
        """Close an open alert. None if the id is unknown or was already acknowledged."""
        inc = acknowledge_incident(self.incidents, event_id, by, now_iso())
        if inc is not None:
            self._changed("acknowledged", event_id)
        return inc

    def camera_changed(self, info: CameraInfo) -> None:
        """Called by a Camera actor when it goes online or offline."""
        self.cameras[info.camera] = info
        self._changed("camera", None)

    def snapshot(self) -> SiteSnapshot:
        return self._snapshot()

    def summary(self, hours: float = 24.0) -> Summary:
        """Windowed stats over the stored incidents (the newest INCIDENTS_MAX)."""
        if not 0 < hours <= 24 * 31:
            raise ValueError("hours must be in (0, 744]")
        return compute_summary(self.incidents, datetime.now(timezone.utc), hours)

    def on_connect(self, socket: SiteSocket) -> None:
        socket.set_tags("dashboard")
        self.set_websocket_auto_response("ping", "pong")
        socket.send(SiteMessage(reason="connect", snapshot=self._snapshot()))

    def on_message(self, socket: SiteSocket, message: SiteCommand) -> None:
        if message.type == "ack" and message.event_id:
            if self.acknowledge(message.event_id, message.by or socket.metadata.name) is not None:
                return  # acknowledge already broadcast to everyone, including this socket
        socket.send(SiteMessage(reason="refresh", event_id=message.event_id, snapshot=self._snapshot()))

    def _changed(self, reason: Literal["recorded", "acknowledged", "camera"], event_id: str | None) -> None:
        self.version += 1
        self.updated_at = now_iso()
        self.broadcast(SiteMessage(reason=reason, event_id=event_id, snapshot=self._snapshot()))

    def _snapshot(self) -> SiteSnapshot:
        s = compute_summary(self.incidents, datetime.now(timezone.utc), 24.0)
        alerts = open_alerts(self.incidents)
        return SiteSnapshot(
            site=self.id,
            version=self.version,
            updated_at=self.updated_at,
            total=self.total,
            by_zone=dict(self.by_zone),
            by_camera=dict(self.by_camera),
            by_type=dict(self.by_type),
            by_severity=dict(self.by_severity),
            compliance_score=s.compliance_score,
            weighted_incidents_per_hour=s.weighted_incidents_per_hour,
            window_hours=s.window_hours,
            open_alerts_count=len(alerts),
            open_alerts=alerts[:SNAPSHOT_OPEN_ALERTS],
            recent=list(reversed(self.incidents[-SNAPSHOT_RECENT:])),
            cameras=dict(self.cameras),
        )


# ---------------------------------------------------------------------------
# Camera actor
# ---------------------------------------------------------------------------


class Camera(Actor):
    """One camera feed. Owns its liveness and forwards incidents to its Site.

    A durable alarm marks the camera offline when heartbeats stop, even if nothing else
    ever calls it again (the runtime wakes the actor for on_alarm). Online/offline flips
    are pushed to the Site so the dashboard snapshot shows camera health live.
    """

    status: CameraStatus | None = persisted(None)
    seen_ids: list[str] = persisted(default_factory=list)
    last_contact_ms: int = persisted(0)

    def heartbeat(self, fps: float | None = None, site: str = DEFAULT_SITE) -> CameraStatus:
        """The camera is alive and producing frames at `fps`."""
        st = self._status(site)
        st.fps = fps
        self._touch(st)
        return st

    def record(self, event: SaesrEvent, site: str = DEFAULT_SITE) -> RecordResult:
        """Record an incident seen by this camera and forward it to the Site.

        If the Site call fails, this call raises and its own changes roll back, so the caller
        retries the whole thing; Site.record is idempotent by event id, so a retry after a
        partial success is harmless (at-least-once delivery, exactly-once effect).
        """
        st = self._status(site)
        duplicate_here = event.id in self.seen_ids
        result = Site.get(st.site).record(event)
        if not duplicate_here:
            remember_id(self.seen_ids, event.id, CAMERA_SEEN_IDS_MAX)
            st.events_seen += 1
            st.last_event = event
        self._touch(st)
        return result

    def get_status(self) -> CameraStatus | None:
        return self.status

    def on_alarm(self) -> None:
        st = self.status
        if st is None:
            return
        idle_ms = _now_ms() - self.last_contact_ms
        if idle_ms >= OFFLINE_AFTER_MS:
            if st.status != "offline":
                st.status = "offline"
                self._notify_site(st)
        else:
            self.alarm.set(self.last_contact_ms + OFFLINE_AFTER_MS)

    def _status(self, site: str) -> CameraStatus:
        if self.status is None:
            self.status = CameraStatus(camera=self.id, site=site)
        self.status.site = site
        return self.status

    def _touch(self, st: CameraStatus) -> None:
        self.last_contact_ms = _now_ms()
        st.last_frame_at = now_iso()
        was_online = st.status == "online"
        st.status = "online"
        self.alarm.set(self.last_contact_ms + OFFLINE_AFTER_MS)
        if not was_online:
            self._notify_site(st)

    def _notify_site(self, st: CameraStatus) -> None:
        Site.get(st.site).camera_changed(
            CameraInfo(camera=st.camera, online=st.status == "online", fps=st.fps, last_seen_at=st.last_frame_at)
        )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
