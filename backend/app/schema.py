"""Shared contract v2 (mirror of contract/event.schema.json and contract/event.ts). Change all three together,
and tell Jas: the dashboard and the relay service build against these shapes.

Event: one detection from the CV worker (POST /events). Incident, Action, TraceEntry: owned by the
CameraActor. ActorMessage: what the relay sends to POST /internal/cameras/{id}/message.
CameraState / SiteState: what the actors broadcast to the dashboard on every change.
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

SITE_ID = "site-demo"
CAMERAS = {"cam-01": "warehouse: lifting and machinery zone", "cam-02": "food line: hygiene"}
ZONES = ("zone-pack-line", "zone-machine-a", "zone-handwash")

EventType = Literal["unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves", "compliant_observation"]
VIOLATIONS: tuple[str, ...] = ("unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves")

IncidentStatus = Literal[
    "verifying", "confirmed", "dismissed", "unverified", "awaiting_approval",
    "actioned", "resolved", "recurring", "closed",
]
RelayStep = Literal["triage", "coach", "fixer", "verifier", "reporter"]
ActionKind = Literal["assign_coaching", "open_corrective_record", "flag_batch_hold", "notify_floor_display", "slow_machine"]
ActionStatus = Literal["pending", "approved", "done", "failed", "awaiting_approval", "rejected"]
# tier 0 = auto, tier 1 = simulated auto, tier 2 = human gate (simulated)
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
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # generated once by the detector
    site_id: str = SITE_ID
    camera_id: str
    type: EventType
    confidence: float = Field(ge=0, le=1)
    timestamp: str = Field(default_factory=now_iso)  # ISO 8601 UTC
    zone_id: str
    bbox: Optional[tuple[float, float, float, float]] = None  # [x, y, w, h] normalized 0..1
    centroid: Optional[Point] = None  # normalized 0..1
    metrics: dict[str, Any] = Field(default_factory=dict)  # e.g. {"torso_angle_deg": 62, "duration_s": 1.4}
    keyframes: list[str] = Field(default_factory=list)  # 3 storage URLs
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
    params: dict[str, Any] = Field(default_factory=dict)
    approved_by: Optional[str] = None
    executed_at: Optional[str] = None


class Incident(BaseModel):
    incident_id: str
    camera_id: str
    type: EventType
    zone_id: str
    status: IncidentStatus = "verifying"
    severity: Optional[int] = Field(default=None, ge=1, le=5)  # set by triage
    event_ids: list[str] = Field(default_factory=list)
    opened_at: str
    updated_at: str
    relay: dict[str, Any] = Field(default_factory=dict)  # {triage, coach, fixer, verifier, reporter} outputs
    actions: list[Action] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    keyframes: list[str] = Field(default_factory=list)  # from the first event
    clip_url: Optional[str] = None
    verify_until: Optional[str] = None  # verification window deadline, set after the last action finishes
    low_confidence: bool = False  # resolved by timeout, no compliant observation seen
    closed_at: Optional[str] = None


# Actor message union (single payload, discriminated by "kind")
class DetectionMsg(BaseModel):
    kind: Literal["detection"] = "detection"
    event: Event


class RelayUpdateMsg(BaseModel):
    kind: Literal["relay_update"] = "relay_update"
    incident_id: str
    step: RelayStep
    status: str  # step outcome, e.g. "done" | "failed"; triage may also set "confirmed" | "dismissed" | "unverified"
    output: dict[str, Any] = Field(default_factory=dict)  # triage output may carry "severity": 1..5
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


class RelayStart(BaseModel):
    """Actor -> POST {RELAY_URL}/relay/start when an incident opens or reaches a new step."""
    incident_id: str
    camera_id: str
    step: RelayStep = "triage"
    event: Event
    keyframes: list[str] = Field(default_factory=list)
    clip_url: Optional[str] = None
    action: Optional[Action] = None  # set when asking the relay to execute an approved gated action


class CameraState(BaseModel):
    camera_id: str
    site_id: str = SITE_ID
    status: CameraStatusT = "online"
    last_heartbeat: Optional[str] = None
    version: int = 0
    recovered_from_version: Optional[int] = None
    compliance_score: float = 100.0
    counters: dict[str, int] = Field(default_factory=dict)  # by event type
    false_positives_filtered: int = 0
    autonomous_fixes: int = 0
    incidents: list[Incident] = Field(default_factory=list)


class SiteState(BaseModel):
    site_id: str = SITE_ID
    site_score: float = 100.0
    active_incidents: int = 0
    autonomous_fixes: int = 0
    cameras: list[CameraState] = Field(default_factory=list)
