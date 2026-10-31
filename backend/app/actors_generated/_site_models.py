from __future__ import annotations
from typing import TYPE_CHECKING, TypeAlias
from durable_actors.generated import UNSET as _UNSET, Unset as _Unset, is_unset as _is_unset
from typing import Annotated, Literal, Optional, Union
from pydantic import Field
from durable_actors.generated import ClientModel
from typing_extensions import TypeAliasType
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

class TraceEntry(ClientModel):
    agent: Annotated[str, Field(title='Agent')]
    model: Annotated[str | None, Field(title='Model')] = None
    started_at: Annotated[str, Field(title='Started At')]
    ended_at: Annotated[str | None, Field(title='Ended At')] = None
    output_summary: Annotated[str | None, Field(title='Output Summary')] = None
    error: Annotated[str | None, Field(title='Error')] = None
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
FieldVersion: TypeAlias = int

class SocketEmittedState(ClientModel):
    """
    Complete current values of the actor's emitted fields.
    """

class SocketStatePatch(ClientModel):
    """
    Changed emitted fields; omitted fields are unchanged.
    """

class ActionInput(ClientModel):
    action_id: Annotated[str, Field(title='Action Id')]
    incident_id: Annotated[str, Field(title='Incident Id')]
    kind: Annotated[Literal['assign_coaching', 'open_corrective_record', 'flag_batch_hold', 'notify_floor_display', 'slow_machine'], Field(title='Kind')]
    status: Annotated[Literal['pending', 'approved', 'done', 'failed', 'awaiting_approval', 'rejected'], Field(title='Status')] = 'pending'
    params: Annotated[dict[str, JsonValueInput] | _Unset, Field(title='Params', exclude_if=_is_unset)] = _UNSET
    approved_by: Annotated[str | None, Field(title='Approved By')] = None
    executed_at: Annotated[str | None, Field(title='Executed At')] = None

class ActionOutput(ClientModel):
    action_id: Annotated[str, Field(title='Action Id')]
    incident_id: Annotated[str, Field(title='Incident Id')]
    kind: Annotated[Literal['assign_coaching', 'open_corrective_record', 'flag_batch_hold', 'notify_floor_display', 'slow_machine'], Field(title='Kind')]
    status: Annotated[Literal['pending', 'approved', 'done', 'failed', 'awaiting_approval', 'rejected'], Field(title='Status')] = 'pending'
    params: Annotated[dict[str, JsonValueOutput] | _Unset, Field(title='Params', exclude_if=_is_unset)] = _UNSET
    approved_by: Annotated[str | None, Field(title='Approved By')] = None
    executed_at: Annotated[str | None, Field(title='Executed At')] = None

class IncidentInput(ClientModel):
    incident_id: Annotated[str, Field(title='Incident Id')]
    camera_id: Annotated[str, Field(title='Camera Id')]
    type: Annotated[Literal['unsafe_lift', 'zone_breach', 'missing_hairnet', 'missing_gloves', 'compliant_observation'], Field(title='Type')]
    zone_id: Annotated[str, Field(title='Zone Id')]
    status: Annotated[Literal['verifying', 'confirmed', 'dismissed', 'unverified', 'awaiting_approval', 'actioned', 'resolved', 'recurring', 'closed'], Field(title='Status')] = 'verifying'
    severity: Annotated[Severity | None, Field(title='Severity')] = None
    event_ids: Annotated[list[str] | _Unset, Field(title='Event Ids', exclude_if=_is_unset)] = _UNSET
    opened_at: Annotated[str, Field(title='Opened At')]
    updated_at: Annotated[str, Field(title='Updated At')]
    relay: Annotated[dict[str, JsonValueInput] | _Unset, Field(title='Relay', exclude_if=_is_unset)] = _UNSET
    actions: Annotated[list[ActionInput] | _Unset, Field(title='Actions', exclude_if=_is_unset)] = _UNSET
    trace: Annotated[list[TraceEntry] | _Unset, Field(title='Trace', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    verify_until: Annotated[str | None, Field(title='Verify Until')] = None
    low_confidence: Annotated[bool, Field(title='Low Confidence')] = False
    closed_at: Annotated[str | None, Field(title='Closed At')] = None

class IncidentOutput(ClientModel):
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
    actions: Annotated[list[ActionOutput] | _Unset, Field(title='Actions', exclude_if=_is_unset)] = _UNSET
    trace: Annotated[list[TraceEntry] | _Unset, Field(title='Trace', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    verify_until: Annotated[str | None, Field(title='Verify Until')] = None
    low_confidence: Annotated[bool, Field(title='Low Confidence')] = False
    closed_at: Annotated[str | None, Field(title='Closed At')] = None

class Action(ClientModel):
    action_id: Annotated[str, Field(title='Action Id')]
    incident_id: Annotated[str, Field(title='Incident Id')]
    kind: Annotated[Literal['assign_coaching', 'open_corrective_record', 'flag_batch_hold', 'notify_floor_display', 'slow_machine'], Field(title='Kind')]
    status: Annotated[Literal['pending', 'approved', 'done', 'failed', 'awaiting_approval', 'rejected'], Field(title='Status')] = 'pending'
    params: Annotated[dict[str, JsonValue] | _Unset, Field(title='Params', exclude_if=_is_unset)] = _UNSET
    approved_by: Annotated[str | None, Field(title='Approved By')] = None
    executed_at: Annotated[str | None, Field(title='Executed At')] = None

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
    relay: Annotated[dict[str, JsonValue] | _Unset, Field(title='Relay', exclude_if=_is_unset)] = _UNSET
    actions: Annotated[list[Action] | _Unset, Field(title='Actions', exclude_if=_is_unset)] = _UNSET
    trace: Annotated[list[TraceEntry] | _Unset, Field(title='Trace', exclude_if=_is_unset)] = _UNSET
    keyframes: Annotated[list[str] | _Unset, Field(title='Keyframes', exclude_if=_is_unset)] = _UNSET
    clip_url: Annotated[str | None, Field(title='Clip Url')] = None
    verify_until: Annotated[str | None, Field(title='Verify Until')] = None
    low_confidence: Annotated[bool, Field(title='Low Confidence')] = False
    closed_at: Annotated[str | None, Field(title='Closed At')] = None

class CameraStateInput(ClientModel):
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
    incidents: Annotated[list[IncidentInput] | _Unset, Field(title='Incidents', exclude_if=_is_unset)] = _UNSET

class CameraStateOutput(ClientModel):
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
    incidents: Annotated[list[IncidentOutput] | _Unset, Field(title='Incidents', exclude_if=_is_unset)] = _UNSET

class SiteState(ClientModel):
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    site_score: Annotated[float, Field(title='Site Score')] = 100.0
    active_incidents: Annotated[int, Field(title='Active Incidents')] = 0
    autonomous_fixes: Annotated[int, Field(title='Autonomous Fixes')] = 0
    cameras: Annotated[list[CameraStateOutput] | _Unset, Field(title='Cameras', exclude_if=_is_unset)] = _UNSET
RpcMethodStateResult: TypeAlias = SiteState
RpcMethodUpdateCameraParameter0: TypeAlias = CameraStateInput

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
    incidents: Annotated[list[Incident] | _Unset, Field(title='Incidents', exclude_if=_is_unset)] = _UNSET

class SocketSiteState(ClientModel):
    site_id: Annotated[str, Field(title='Site Id')] = 'site-demo'
    site_score: Annotated[float, Field(title='Site Score')] = 100.0
    active_incidents: Annotated[int, Field(title='Active Incidents')] = 0
    autonomous_fixes: Annotated[int, Field(title='Autonomous Fixes')] = 0
    cameras: Annotated[list[CameraState] | _Unset, Field(title='Cameras', exclude_if=_is_unset)] = _UNSET
SocketOutgoing: TypeAlias = SocketSiteState
FieldCameras: TypeAlias = dict[str, CameraState]

class SocketState(ClientModel):
    """
    Public persisted fields of the actor.
    """
    cameras: FieldCameras
    version: FieldVersion

class ContractTypes(ClientModel):
    """
    Named wire types used by this generated actor client.
    """
    RpcMethod_state_Result: RpcMethodStateResult
    RpcMethod_update_camera_Parameter_0: RpcMethodUpdateCameraParameter0
    SocketMetadata_1: Annotated[SocketMetadata, Field(alias='SocketMetadata')]
    SocketIncoming_1: Annotated[SocketIncoming, Field(alias='SocketIncoming')]
    SocketOutgoing_1: Annotated[SocketOutgoing, Field(alias='SocketOutgoing')]
    SocketState_1: Annotated[SocketState, Field(alias='SocketState')]
    SocketEmittedState_1: Annotated[SocketEmittedState, Field(alias='SocketEmittedState')]
    SocketStatePatch_1: Annotated[SocketStatePatch, Field(alias='SocketStatePatch')]
