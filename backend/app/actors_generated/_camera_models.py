from __future__ import annotations
from typing import TYPE_CHECKING, TypeAlias
from durable_actors.generated import UNSET as _UNSET, Unset as _Unset, is_unset as _is_unset
from typing import Annotated, Literal, Optional, Union
from pydantic import Field
from durable_actors.generated import ClientModel
from typing_extensions import TypeAliasType
if TYPE_CHECKING:
    Bbox: TypeAlias = Annotated[tuple[float, float, float, float], Field(max_length=4, min_length=4, title='Bbox')]
else:
    Bbox = TypeAliasType('Bbox', Annotated[tuple[float, float, float, float], Field(max_length=4, min_length=4, title='Bbox')])
if TYPE_CHECKING:
    Severity: TypeAlias = Annotated[int, Field(ge=1, le=5, title='Severity')]
else:
    Severity = TypeAliasType('Severity', Annotated[int, Field(ge=1, le=5, title='Severity')])
if TYPE_CHECKING:
    JsonValueInput: TypeAlias = Optional[Union[str, int, float, bool, list['JsonValueInput'], dict[str, 'JsonValueInput']]]
else:
    JsonValueInput = TypeAliasType('JsonValueInput', Optional[Union[str, int, float, bool, list['JsonValueInput'], dict[str, 'JsonValueInput']]])
if TYPE_CHECKING:
    JsonValueOutput: TypeAlias = Optional[Union[str, int, float, bool, list['JsonValueOutput'], dict[str, 'JsonValueOutput']]]
else:
    JsonValueOutput = TypeAliasType('JsonValueOutput', Optional[Union[str, int, float, bool, list['JsonValueOutput'], dict[str, 'JsonValueOutput']]])

class Point(ClientModel):
    x: Annotated[float, Field(title='X')]
    y: Annotated[float, Field(title='Y')]

class TraceEntry(ClientModel):
    agent: Annotated[str, Field(title='Agent')]
    model: Annotated[str | None, Field(title='Model')] = None
    started_at: Annotated[str, Field(title='Started At')]
    ended_at: Annotated[str | None, Field(title='Ended At')] = None
    output_summary: Annotated[str | None, Field(title='Output Summary')] = None
    error: Annotated[str | None, Field(title='Error')] = None
RpcMethodMessageParameter0: TypeAlias = JsonValueInput
RpcMethodRelayDeliveredParameter0: TypeAlias = str
RpcMethodRelayDeliveredResult: TypeAlias = bool
if TYPE_CHECKING:
    JsonValue: TypeAlias = Optional[Union[str, int, float, bool, list['JsonValue'], dict[str, 'JsonValue']]]
else:
    JsonValue = TypeAliasType('JsonValue', Optional[Union[str, int, float, bool, list['JsonValue'], dict[str, 'JsonValue']]])

class SocketCommand(ClientModel):
    """
    What a socket may send: {"type": "refresh"} -> that socket gets the current state again.
    """
    type: Annotated[Literal['refresh'], Field(title='Type')] = 'refresh'

class Viewer(ClientModel):
    """
    Socket metadata: who is watching.
    """
    name: Annotated[str, Field(title='Name')] = 'dashboard'
SocketMetadata: TypeAlias = Viewer
SocketIncoming: TypeAlias = SocketCommand
Tag: TypeAlias = str

class SocketEmittedState(ClientModel):
    """
    Complete current values of the actor's emitted fields.
    """

class SocketStatePatch(ClientModel):
    """
    Changed emitted fields; omitted fields are unchanged.
    """

class Action(ClientModel):
    action_id: Annotated[str, Field(title='Action Id')]
    incident_id: Annotated[str, Field(title='Incident Id')]
    kind: Annotated[Literal['assign_coaching', 'open_corrective_record', 'flag_batch_hold', 'notify_floor_display', 'slow_machine'], Field(title='Kind')]
    status: Annotated[Literal['pending', 'approved', 'done', 'failed', 'awaiting_approval', 'rejected'], Field(title='Status')] = 'pending'
    params: Annotated[dict[str, JsonValueOutput] | _Unset, Field(title='Params', exclude_if=_is_unset)] = _UNSET
    approved_by: Annotated[str | None, Field(title='Approved By')] = None
    executed_at: Annotated[str | None, Field(title='Executed At')] = None

class Event(ClientModel):
    event_id: Annotated[str | _Unset, Field(title='Event Id', exclude_if=_is_unset)] = _UNSET
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    camera_id: Annotated[str, Field(title='Camera Id')]
    type: Annotated[Literal['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'compliant_observation'], Field(title='Type')]
    confidence: Annotated[float, Field(ge=0.0, le=1.0, title='Confidence')]
    timestamp: Annotated[str | _Unset, Field(title='Timestamp', exclude_if=_is_unset)] = _UNSET
    zone_id: Annotated[str, Field(title='Zone Id')]
    bbox: Annotated[Bbox | None, Field(title='Bbox')] = None
    centroid: Point | None = None
    metrics: Annotated[dict[str, JsonValueOutput] | _Unset, Field(title='Metrics', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None

class Incident(ClientModel):
    incident_id: Annotated[str, Field(title='Incident Id')]
    camera_id: Annotated[str, Field(title='Camera Id')]
    type: Annotated[Literal['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'compliant_observation'], Field(title='Type')]
    zone_id: Annotated[str, Field(title='Zone Id')]
    status: Annotated[Literal['verifying', 'confirmed', 'dismissed', 'unverified', 'awaiting_approval', 'actioned', 'resolved', 'recurring', 'closed'], Field(title='Status')] = 'verifying'
    severity: Annotated[Severity | None, Field(title='Severity')] = None
    event_ids: Annotated[list[str] | _Unset, Field(title='Event Ids', exclude_if=_is_unset)] = _UNSET
    opened_at: Annotated[str, Field(title='Opened At')]
    updated_at: Annotated[str, Field(title='Updated At')]
    relay: Annotated[dict[str, JsonValueOutput] | _Unset, Field(title='Relay', exclude_if=_is_unset)] = _UNSET
    actions: Annotated[list[Action] | _Unset, Field(title='Actions', exclude_if=_is_unset)] = _UNSET
    trace: Annotated[list[TraceEntry] | _Unset, Field(title='Trace', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    verify_until: Annotated[str | None, Field(title='Verify Until')] = None
    low_confidence: Annotated[bool, Field(title='Low Confidence')] = False
    closed_at: Annotated[str | None, Field(title='Closed At')] = None

class SocketAction(ClientModel):
    action_id: Annotated[str, Field(title='Action Id')]
    incident_id: Annotated[str, Field(title='Incident Id')]
    kind: Annotated[Literal['assign_coaching', 'open_corrective_record', 'flag_batch_hold', 'notify_floor_display', 'slow_machine'], Field(title='Kind')]
    status: Annotated[Literal['pending', 'approved', 'done', 'failed', 'awaiting_approval', 'rejected'], Field(title='Status')] = 'pending'
    params: Annotated[dict[str, JsonValue] | _Unset, Field(title='Params', exclude_if=_is_unset)] = _UNSET
    approved_by: Annotated[str | None, Field(title='Approved By')] = None
    executed_at: Annotated[str | None, Field(title='Executed At')] = None

class SocketEvent(ClientModel):
    event_id: Annotated[str | _Unset, Field(title='Event Id', exclude_if=_is_unset)] = _UNSET
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    camera_id: Annotated[str, Field(title='Camera Id')]
    type: Annotated[Literal['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'compliant_observation'], Field(title='Type')]
    confidence: Annotated[float, Field(ge=0.0, le=1.0, title='Confidence')]
    timestamp: Annotated[str | _Unset, Field(title='Timestamp', exclude_if=_is_unset)] = _UNSET
    zone_id: Annotated[str, Field(title='Zone Id')]
    bbox: Annotated[Bbox | None, Field(title='Bbox')] = None
    centroid: Point | None = None
    metrics: Annotated[dict[str, JsonValue] | _Unset, Field(title='Metrics', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None

class SocketIncident(ClientModel):
    incident_id: Annotated[str, Field(title='Incident Id')]
    camera_id: Annotated[str, Field(title='Camera Id')]
    type: Annotated[Literal['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'compliant_observation'], Field(title='Type')]
    zone_id: Annotated[str, Field(title='Zone Id')]
    status: Annotated[Literal['verifying', 'confirmed', 'dismissed', 'unverified', 'awaiting_approval', 'actioned', 'resolved', 'recurring', 'closed'], Field(title='Status')] = 'verifying'
    severity: Annotated[Severity | None, Field(title='Severity')] = None
    event_ids: Annotated[list[str] | _Unset, Field(title='Event Ids', exclude_if=_is_unset)] = _UNSET
    opened_at: Annotated[str, Field(title='Opened At')]
    updated_at: Annotated[str, Field(title='Updated At')]
    relay: Annotated[dict[str, JsonValue] | _Unset, Field(title='Relay', exclude_if=_is_unset)] = _UNSET
    actions: Annotated[list[SocketAction] | _Unset, Field(title='Actions', exclude_if=_is_unset)] = _UNSET
    trace: Annotated[list[TraceEntry] | _Unset, Field(title='Trace', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    verify_until: Annotated[str | None, Field(title='Verify Until')] = None
    low_confidence: Annotated[bool, Field(title='Low Confidence')] = False
    closed_at: Annotated[str | None, Field(title='Closed At')] = None

class CameraState(ClientModel):
    camera_id: Annotated[str, Field(title='Camera Id')]
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    status: Annotated[Literal['online', 'offline', 'recovering'], Field(title='Status')] = 'online'
    last_heartbeat: Annotated[str | None, Field(title='Last Heartbeat')] = None
    version: Annotated[int, Field(title='Version')] = 0
    recovered_from_version: Annotated[int | None, Field(title='Recovered From Version')] = None
    compliance_score: Annotated[float, Field(title='Compliance Score')] = 100.0
    counters: Annotated[dict[str, int] | _Unset, Field(title='Counters', exclude_if=_is_unset)] = _UNSET
    false_positives_filtered: Annotated[int, Field(title='False Positives Filtered')] = 0
    autonomous_fixes: Annotated[int, Field(title='Autonomous Fixes')] = 0
    incidents: Annotated[list[SocketIncident] | _Unset, Field(title='Incidents', exclude_if=_is_unset)] = _UNSET

class RelayStart(ClientModel):
    """
    Actor -> POST {RELAY_URL}/relay/start when an incident opens or reaches a new step.
    """
    incident_id: Annotated[str, Field(title='Incident Id')]
    camera_id: Annotated[str, Field(title='Camera Id')]
    step: Annotated[Literal['triage', 'coach', 'fixer', 'verifier', 'reporter'], Field(title='Step')] = 'triage'
    event: SocketEvent
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    action: SocketAction | None = None
RpcMethodStateResult: TypeAlias = CameraState
SocketOutgoing: TypeAlias = CameraState

class OutboxItem(ClientModel):
    """
    One relay call the backend dispatcher must POST to {RELAY_URL}/relay/start.

    `id` is stable (incident:step[:rN] or incident:exec:action) and doubles as the
    X-Idempotency-Key header, so the relay can drop a resend it already accepted.
    """
    id: Annotated[str, Field(title='Id')]
    created_at: Annotated[str, Field(title='Created At')]
    start: RelayStart
RpcMethodPendingRelayResult: TypeAlias = list[OutboxItem]

class CameraCore(ClientModel):
    """
    Everything a Camera actor persists. CameraState is derived from it.
    """
    camera_id: Annotated[str, Field(title='Camera Id')]
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    status: Annotated[Literal['online', 'offline', 'recovering'], Field(title='Status')] = 'online'
    last_heartbeat: Annotated[str | None, Field(title='Last Heartbeat')] = None
    version: Annotated[int, Field(title='Version')] = 0
    recovered_from_version: Annotated[int | None, Field(title='Recovered From Version')] = None
    counters: Annotated[dict[str, int] | _Unset, Field(title='Counters', exclude_if=_is_unset)] = _UNSET
    false_positives_filtered: Annotated[int, Field(title='False Positives Filtered')] = 0
    autonomous_fixes: Annotated[int, Field(title='Autonomous Fixes')] = 0
    incidents: Annotated[list[SocketIncident] | _Unset, Field(title='Incidents', exclude_if=_is_unset)] = _UNSET
    seen_event_ids: Annotated[list[str] | _Unset, Field(title='Seen Event Ids', exclude_if=_is_unset)] = _UNSET
    outbox: Annotated[list[OutboxItem] | _Unset, Field(title='Outbox', exclude_if=_is_unset)] = _UNSET
    delivered_ids: Annotated[list[str] | _Unset, Field(title='Delivered Ids', exclude_if=_is_unset)] = _UNSET
    incident_events: Annotated[dict[str, SocketEvent] | _Unset, Field(title='Incident Events', exclude_if=_is_unset)] = _UNSET
    last_event_ms: Annotated[dict[str, int] | _Unset, Field(title='Last Event Ms', exclude_if=_is_unset)] = _UNSET
    escalations: Annotated[dict[str, int] | _Unset, Field(title='Escalations', exclude_if=_is_unset)] = _UNSET
    last_contact_ms: Annotated[int, Field(title='Last Contact Ms')] = 0
    recovering_until_ms: Annotated[int | None, Field(title='Recovering Until Ms')] = None
    boot_id: Annotated[str, Field(title='Boot Id')] = ''
FieldCore: TypeAlias = CameraCore | None

class SocketState(ClientModel):
    """
    Public persisted fields of the actor.
    """
    core: FieldCore

class MessageResult(ClientModel):
    """
    Result of Camera.message. changed=False means the message was a no-op (duplicate).
    """
    ok: Annotated[bool, Field(title='Ok')] = True
    changed: Annotated[bool, Field(title='Changed')]
    version: Annotated[int, Field(title='Version')]
    state: CameraState
    outbox: Annotated[list[OutboxItem], Field(title='Outbox')]
RpcMethodMessageResult: TypeAlias = MessageResult

class ContractTypes(ClientModel):
    """
    Named wire types used by this generated actor client.
    """
    RpcMethod_message_Parameter_0: RpcMethodMessageParameter0
    RpcMethod_message_Result: RpcMethodMessageResult
    RpcMethod_pending_relay_Result: RpcMethodPendingRelayResult
    RpcMethod_relay_delivered_Parameter_0: RpcMethodRelayDeliveredParameter0
    RpcMethod_relay_delivered_Result: RpcMethodRelayDeliveredResult
    RpcMethod_state_Result: RpcMethodStateResult
    SocketMetadata_1: Annotated[SocketMetadata, Field(alias='SocketMetadata')]
    SocketIncoming_1: Annotated[SocketIncoming, Field(alias='SocketIncoming')]
    SocketOutgoing_1: Annotated[SocketOutgoing, Field(alias='SocketOutgoing')]
    SocketState_1: Annotated[SocketState, Field(alias='SocketState')]
    SocketEmittedState_1: Annotated[SocketEmittedState, Field(alias='SocketEmittedState')]
    SocketStatePatch_1: Annotated[SocketStatePatch, Field(alias='SocketStatePatch')]
