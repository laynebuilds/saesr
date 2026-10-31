"""Typed async actor handles sharing the synchronous namespace models."""
from __future__ import annotations
import builtins as _builtins
from typing import TypeAlias as _TypeAlias, Literal as _Literal
from durable_actors.async_client import AsyncActorTransport as _ActorTransport
from durable_actors.generated import Unset as _Unset
from durable_actors.proxy import prepare_async_authorization as _prepare_authorization
from durable_actors.client import SocketGrant as _SocketGrant
from pydantic import TypeAdapter as _TypeAdapter
from . import actors as _sync_actors
from . import _camera as _client_0
from . import _camera_models as _models_0

class Camera:
    """One camera feed. Owns its liveness and forwards incidents to its Site.
    
    A durable alarm marks the camera offline when heartbeats stop, even if nothing else
    ever calls it again (the runtime wakes the actor for on_alarm). Online/offline flips
    are pushed to the Site so the dashboard snapshot shows camera health live.
    """
    Stub: _TypeAlias = _client_0.AsyncStub
    Metadata: _TypeAlias = _models_0.SocketMetadata
    Incoming: _TypeAlias = _models_0.SocketIncoming
    Outgoing: _TypeAlias = _models_0.SocketOutgoing
    State: _TypeAlias = _models_0.SocketState
    EmittedState: _TypeAlias = _models_0.SocketEmittedState
    StatePatch: _TypeAlias = _models_0.SocketStatePatch
    RecordResult: _TypeAlias = _models_0.RecordResult
    SaesrEvent: _TypeAlias = _models_0.SaesrEvent
    CameraStatus: _TypeAlias = _models_0.CameraStatus

    @_builtins.staticmethod
    def get(actor_id: _builtins.str, transport: _ActorTransport | None = None) -> _client_0.AsyncStub:
        """Return a async actor handle without sending a request.
        
        Args:
            actor_id: Identity of the actor to call.
            transport: Optional caller-owned transport. Uses the SDK-managed
                transport when omitted.
        
        The returned Stub provides the actor's typed RPC methods.
        """
        return _client_0.AsyncStub(actor_id, transport)

    Authorization: _TypeAlias = _sync_actors.Camera.Authorization

    @_builtins.staticmethod
    async def prepare_websocket(authorization: Camera.Authorization, transport: _ActorTransport | None = None) -> _SocketGrant:
        """Issue browser access after authenticating the user and authorizing this actor."""
        return await _prepare_authorization(_TypeAdapter(Camera.Authorization).validate_python(authorization), transport)

    class Methods:
        """Argument tuples and return types for each actor RPC."""
        class get_status:
            """Types for Camera.get_status."""
            Args: _TypeAlias = _builtins.tuple[()]
            Result: _TypeAlias = _models_0.RpcMethodGetStatusResult

        class heartbeat:
            """The camera is alive and producing frames at `fps`."""
            Args: _TypeAlias = _builtins.tuple[()] | _builtins.tuple[_models_0.RpcMethodHeartbeatParameter0 | _Unset] | _builtins.tuple[_models_0.RpcMethodHeartbeatParameter0 | _Unset, _models_0.RpcMethodHeartbeatParameter1 | _Unset]
            Result: _TypeAlias = _models_0.RpcMethodHeartbeatResult

        class record:
            """Record an incident seen by this camera and forward it to the Site.
            
            If the Site call fails, this call raises and its own changes roll back, so the caller
            retries the whole thing; Site.record is idempotent by event id, so a retry after a
            partial success is harmless (at-least-once delivery, exactly-once effect).
            """
            Args: _TypeAlias = _builtins.tuple[_models_0.RpcMethodRecordParameter0] | _builtins.tuple[_models_0.RpcMethodRecordParameter0, _models_0.RpcMethodRecordParameter1 | _Unset]
            Result: _TypeAlias = _models_0.RpcMethodRecordResult

from . import _site as _client_1
from . import _site_models as _models_1

class Site:
    """One warehouse. Single writer for its incidents, counters, score and open alerts.
    
    Serialized execution: record, acknowledge and camera_changed never overlap for the same
    Site id. Many Camera actors call Site.record concurrently; the runtime queues those calls
    and applies them one at a time, so no update is ever lost and every broadcast snapshot is
    a consistent state (counters, incidents and open alerts always agree).
    """
    Stub: _TypeAlias = _client_1.AsyncStub
    Metadata: _TypeAlias = _models_1.SocketMetadata
    Incoming: _TypeAlias = _models_1.SocketIncoming
    Outgoing: _TypeAlias = _models_1.SocketOutgoing
    State: _TypeAlias = _models_1.SocketState
    EmittedState: _TypeAlias = _models_1.SocketEmittedState
    StatePatch: _TypeAlias = _models_1.SocketStatePatch
    CameraInfo: _TypeAlias = _models_1.CameraInfo
    RecordResult: _TypeAlias = _models_1.RecordResult
    SaesrEvent: _TypeAlias = _models_1.SaesrEvent
    Summary: _TypeAlias = _models_1.Summary
    SiteCommand: _TypeAlias = _models_1.SiteCommand
    Viewer: _TypeAlias = _models_1.Viewer
    Incident: _TypeAlias = _models_1.Incident
    SiteSnapshot: _TypeAlias = _models_1.SiteSnapshot
    SiteMessage: _TypeAlias = _models_1.SiteMessage

    @_builtins.staticmethod
    def get(actor_id: _builtins.str, transport: _ActorTransport | None = None) -> _client_1.AsyncStub:
        """Return a async actor handle without sending a request.
        
        Args:
            actor_id: Identity of the actor to call.
            transport: Optional caller-owned transport. Uses the SDK-managed
                transport when omitted.
        
        The returned Stub provides the actor's typed RPC methods.
        """
        return _client_1.AsyncStub(actor_id, transport)

    Authorization: _TypeAlias = _sync_actors.Site.Authorization

    @_builtins.staticmethod
    async def prepare_websocket(authorization: Site.Authorization, transport: _ActorTransport | None = None) -> _SocketGrant:
        """Issue browser access after authenticating the user and authorizing this actor."""
        return await _prepare_authorization(_TypeAdapter(Site.Authorization).validate_python(authorization), transport)

    class Methods:
        """Argument tuples and return types for each actor RPC."""
        class acknowledge:
            """Close an open alert. None if the id is unknown or was already acknowledged."""
            Args: _TypeAlias = _builtins.tuple[_models_1.RpcMethodAcknowledgeParameter0, _models_1.RpcMethodAcknowledgeParameter1]
            Result: _TypeAlias = _models_1.RpcMethodAcknowledgeResult

        class camera_changed:
            """Called by a Camera actor when it goes online or offline."""
            Args: _TypeAlias = _builtins.tuple[_models_1.RpcMethodCameraChangedParameter0]
            Result: _TypeAlias = None

        class record:
            """Record one incident. Idempotent by event id: a retry of the same event changes nothing."""
            Args: _TypeAlias = _builtins.tuple[_models_1.RpcMethodRecordParameter0]
            Result: _TypeAlias = _models_1.RpcMethodRecordResult

        class snapshot:
            """Types for Site.snapshot."""
            Args: _TypeAlias = _builtins.tuple[()]
            Result: _TypeAlias = _models_1.RpcMethodSnapshotResult

        class summary:
            """Windowed stats over the stored incidents (the newest INCIDENTS_MAX)."""
            Args: _TypeAlias = _builtins.tuple[()] | _builtins.tuple[_models_1.RpcMethodSummaryParameter0 | _Unset]
            Result: _TypeAlias = _models_1.RpcMethodSummaryResult

__all__ = ['Camera', 'Site']
