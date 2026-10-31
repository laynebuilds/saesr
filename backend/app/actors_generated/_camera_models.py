from __future__ import annotations
from typing import TYPE_CHECKING, TypeAlias
from typing import Annotated, Literal, Optional, Union
from pydantic import Field
from durable_actors.generated import ClientModel
from typing_extensions import TypeAliasType

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
RpcMethodHeartbeatParameter0: TypeAlias = float | None
RpcMethodHeartbeatParameter1: TypeAlias = str
RpcMethodRecordParameter0: TypeAlias = SaesrEvent
RpcMethodRecordParameter1: TypeAlias = str
RpcMethodRecordResult: TypeAlias = RecordResult
if TYPE_CHECKING:
    JsonValueInput: TypeAlias = Optional[Union[str, int, float, bool, list['JsonValueInput'], dict[str, 'JsonValueInput']]]
else:
    JsonValueInput = TypeAliasType('JsonValueInput', Optional[Union[str, int, float, bool, list['JsonValueInput'], dict[str, 'JsonValueInput']]])
if TYPE_CHECKING:
    JsonValueOutput: TypeAlias = Optional[Union[str, int, float, bool, list['JsonValueOutput'], dict[str, 'JsonValueOutput']]]
else:
    JsonValueOutput = TypeAliasType('JsonValueOutput', Optional[Union[str, int, float, bool, list['JsonValueOutput'], dict[str, 'JsonValueOutput']]])
SocketMetadata: TypeAlias = JsonValueInput
SocketIncoming: TypeAlias = JsonValueInput
SocketOutgoing: TypeAlias = JsonValueOutput
Tag: TypeAlias = str
FieldSeenIds: TypeAlias = list[str]
FieldLastContactMs: TypeAlias = int

class SocketEmittedState(ClientModel):
    """
    Complete current values of the actor's emitted fields.
    """

class SocketStatePatch(ClientModel):
    """
    Changed emitted fields; omitted fields are unchanged.
    """

class CameraStatus(ClientModel):
    """
    Durable status of one camera.
    """
    camera: Annotated[str, Field(title='Camera')]
    site: Annotated[str, Field(title='Site')] = 'demo-warehouse'
    status: Annotated[Literal['online', 'offline', 'unknown'], Field(title='Status')] = 'unknown'
    fps: Annotated[float | None, Field(title='Fps')] = None
    last_frame_at: Annotated[str | None, Field(title='Last Frame At')] = None
    last_event: SaesrEvent | None = None
    events_seen: Annotated[int, Field(title='Events Seen')] = 0
RpcMethodGetStatusResult: TypeAlias = CameraStatus | None
RpcMethodHeartbeatResult: TypeAlias = CameraStatus
FieldStatus: TypeAlias = CameraStatus | None

class SocketState(ClientModel):
    """
    Public persisted fields of the actor.
    """
    status: FieldStatus
    seen_ids: FieldSeenIds
    last_contact_ms: FieldLastContactMs

class ContractTypes(ClientModel):
    """
    Named wire types used by this generated actor client.
    """
    RpcMethod_get_status_Result: RpcMethodGetStatusResult
    RpcMethod_heartbeat_Parameter_0: RpcMethodHeartbeatParameter0
    RpcMethod_heartbeat_Parameter_1: RpcMethodHeartbeatParameter1
    RpcMethod_heartbeat_Result: RpcMethodHeartbeatResult
    RpcMethod_record_Parameter_0: RpcMethodRecordParameter0
    RpcMethod_record_Parameter_1: RpcMethodRecordParameter1
    RpcMethod_record_Result: RpcMethodRecordResult
    SocketMetadata_1: Annotated[SocketMetadata, Field(alias='SocketMetadata')]
    SocketIncoming_1: Annotated[SocketIncoming, Field(alias='SocketIncoming')]
    SocketOutgoing_1: Annotated[SocketOutgoing, Field(alias='SocketOutgoing')]
    SocketState_1: Annotated[SocketState, Field(alias='SocketState')]
    SocketEmittedState_1: Annotated[SocketEmittedState, Field(alias='SocketEmittedState')]
    SocketStatePatch_1: Annotated[SocketStatePatch, Field(alias='SocketStatePatch')]
