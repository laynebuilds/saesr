from __future__ import annotations
from typing import TYPE_CHECKING, TypeAlias
from typing import Annotated, Literal
from pydantic import Field
from durable_actors.generated import ClientModel
from typing_extensions import TypeAliasType

class CameraInfo(ClientModel):
    """
    Per-camera status as seen by the Site (updated when a camera goes online or offline).
    """
    camera: Annotated[str, Field(title='Camera')]
    online: Annotated[bool, Field(title='Online')]
    fps: Annotated[float | None, Field(title='Fps')] = None
    last_seen_at: Annotated[str | None, Field(title='Last Seen At')] = None

class RecordResult(ClientModel):
    """
    Result of Site.record. duplicate=True means the event id was already recorded (no change).
    """
    event_id: Annotated[str, Field(title='Event Id')]
    duplicate: Annotated[bool, Field(title='Duplicate')]
    total: Annotated[int, Field(title='Total')]

class SaesrEvent(ClientModel):
    """
    One detected incident, as published by the CV pipeline.
    """
    id: Annotated[str, Field(title='Id')]
    camera: Annotated[str, Field(title='Camera')]
    type: Annotated[Literal['unsafe_lift', 'no_hardhat', 'no_vest', 'restricted_zone', 'no_hairnet', 'no_gloves'], Field(title='Type')]
    severity: Annotated[Literal['low', 'medium', 'high'], Field(title='Severity')]
    timestamp: Annotated[str, Field(title='Timestamp')]
    zone: Annotated[str, Field(title='Zone')]
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    frame_url: Annotated[str | None, Field(title='Frame Url')] = None
    confidence: Annotated[float | None, Field(title='Confidence')] = None
    bbox: Annotated[list[float] | None, Field(title='Bbox')] = None
    detail: Annotated[str | None, Field(title='Detail')] = None

class Summary(ClientModel):
    """
    Same shape as GET /summary on the backend (minus `mode`).
    """
    total: Annotated[int, Field(title='Total')]
    window_hours: Annotated[float, Field(title='Window Hours')]
    by_type: Annotated[dict[str, int], Field(title='By Type')]
    by_zone: Annotated[dict[str, int], Field(title='By Zone')]
    by_camera: Annotated[dict[str, int], Field(title='By Camera')]
    by_severity: Annotated[dict[str, int], Field(title='By Severity')]
    weighted_incidents_per_hour: Annotated[float, Field(title='Weighted Incidents Per Hour')]
    compliance_score: Annotated[float, Field(title='Compliance Score')]
    last_event: SaesrEvent | None = None
RpcMethodAcknowledgeParameter0: TypeAlias = str
RpcMethodAcknowledgeParameter1: TypeAlias = str
RpcMethodCameraChangedParameter0: TypeAlias = CameraInfo
RpcMethodRecordParameter0: TypeAlias = SaesrEvent
RpcMethodRecordResult: TypeAlias = RecordResult
RpcMethodSummaryParameter0: TypeAlias = float
RpcMethodSummaryResult: TypeAlias = Summary

class SiteCommand(ClientModel):
    """
    What a dashboard socket may send.

    {"type": "refresh"} -> the socket receives a fresh snapshot.
    {"type": "ack", "event_id": "...", "by": "jas"} -> acknowledge an alert; everyone gets the new snapshot.
    """
    type: Annotated[Literal['refresh', 'ack'], Field(title='Type')]
    event_id: Annotated[str | None, Field(title='Event Id')] = None
    by: Annotated[str | None, Field(title='By')] = None

class Viewer(ClientModel):
    """
    Socket metadata: who is watching.
    """
    name: Annotated[str, Field(title='Name')] = 'dashboard'
SocketMetadata: TypeAlias = Viewer
SocketIncoming: TypeAlias = SiteCommand
Tag: TypeAlias = str
FieldSeenIds: TypeAlias = list[str]
FieldTotal: TypeAlias = int
FieldByZone: TypeAlias = dict[str, int]
FieldByCamera: TypeAlias = dict[str, int]
FieldByType: TypeAlias = dict[str, int]
FieldBySeverity: TypeAlias = dict[str, int]
FieldCameras: TypeAlias = dict[str, CameraInfo]
FieldVersion: TypeAlias = int
FieldUpdatedAt: TypeAlias = str | None

class SocketEmittedState(ClientModel):
    """
    Complete current values of the actor's emitted fields.
    """

class SocketStatePatch(ClientModel):
    """
    Changed emitted fields; omitted fields are unchanged.
    """

class Incident(ClientModel):
    """
    An event as stored by the Site, plus its acknowledgement.
    """
    event: SaesrEvent
    recorded_at: Annotated[str, Field(title='Recorded At')]
    acknowledged_by: Annotated[str | None, Field(title='Acknowledged By')] = None
    acknowledged_at: Annotated[str | None, Field(title='Acknowledged At')] = None

class SiteSnapshot(ClientModel):
    """
    Everything a dashboard needs to render one warehouse.
    """
    site: Annotated[str, Field(title='Site')]
    version: Annotated[int, Field(title='Version')]
    '\n    Increments on every change; use it to drop out-of-order messages.\n    '
    updated_at: Annotated[str | None, Field(title='Updated At')]
    total: Annotated[int, Field(title='Total')]
    '\n    Lifetime incidents recorded (not windowed).\n    '
    by_zone: Annotated[dict[str, int], Field(title='By Zone')]
    by_camera: Annotated[dict[str, int], Field(title='By Camera')]
    by_type: Annotated[dict[str, int], Field(title='By Type')]
    by_severity: Annotated[dict[str, int], Field(title='By Severity')]
    compliance_score: Annotated[float, Field(title='Compliance Score')]
    '\n    24h compliance score, 0..100.\n    '
    weighted_incidents_per_hour: Annotated[float, Field(title='Weighted Incidents Per Hour')]
    window_hours: Annotated[float, Field(title='Window Hours')]
    open_alerts_count: Annotated[int, Field(title='Open Alerts Count')]
    open_alerts: Annotated[list[Incident], Field(title='Open Alerts')]
    '\n    Newest unacknowledged incidents, newest first.\n    '
    recent: Annotated[list[Incident], Field(title='Recent')]
    '\n    Newest incidents, newest first.\n    '
    cameras: Annotated[dict[str, CameraInfo], Field(title='Cameras')]
RpcMethodAcknowledgeResult: TypeAlias = Incident | None
RpcMethodSnapshotResult: TypeAlias = SiteSnapshot

class SiteMessage(ClientModel):
    """
    What a Site socket receives: always a full snapshot, tagged with why it was sent.
    """
    type: Annotated[Literal['snapshot'], Field(title='Type')] = 'snapshot'
    reason: Annotated[Literal['connect', 'refresh', 'recorded', 'acknowledged', 'camera'], Field(title='Reason')]
    event_id: Annotated[str | None, Field(title='Event Id')] = None
    snapshot: SiteSnapshot
SocketOutgoing: TypeAlias = SiteMessage
FieldIncidents: TypeAlias = list[Incident]

class SocketState(ClientModel):
    """
    Public persisted fields of the actor.
    """
    incidents: FieldIncidents
    seen_ids: FieldSeenIds
    total: FieldTotal
    by_zone: FieldByZone
    by_camera: FieldByCamera
    by_type: FieldByType
    by_severity: FieldBySeverity
    cameras: FieldCameras
    version: FieldVersion
    updated_at: FieldUpdatedAt

class ContractTypes(ClientModel):
    """
    Named wire types used by this generated actor client.
    """
    RpcMethod_acknowledge_Parameter_0: RpcMethodAcknowledgeParameter0
    RpcMethod_acknowledge_Parameter_1: RpcMethodAcknowledgeParameter1
    RpcMethod_acknowledge_Result: RpcMethodAcknowledgeResult
    RpcMethod_camera_changed_Parameter_0: RpcMethodCameraChangedParameter0
    RpcMethod_record_Parameter_0: RpcMethodRecordParameter0
    RpcMethod_record_Result: RpcMethodRecordResult
    RpcMethod_snapshot_Result: RpcMethodSnapshotResult
    RpcMethod_summary_Parameter_0: RpcMethodSummaryParameter0
    RpcMethod_summary_Result: RpcMethodSummaryResult
    SocketMetadata_1: Annotated[SocketMetadata, Field(alias='SocketMetadata')]
    SocketIncoming_1: Annotated[SocketIncoming, Field(alias='SocketIncoming')]
    SocketOutgoing_1: Annotated[SocketOutgoing, Field(alias='SocketOutgoing')]
    SocketState_1: Annotated[SocketState, Field(alias='SocketState')]
    SocketEmittedState_1: Annotated[SocketEmittedState, Field(alias='SocketEmittedState')]
    SocketStatePatch_1: Annotated[SocketStatePatch, Field(alias='SocketStatePatch')]
