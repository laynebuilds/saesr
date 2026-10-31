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
    """One camera. Owns its incidents, the relay outbox and the verification windows.
    
    Serialized execution means a detection, a relay update and an approval for this camera never
    interleave. A single durable alarm is always set to the earliest pending deadline (offline
    timeout, end of recovering, verification window), so time-driven transitions happen even if
    nothing else ever calls the actor.
    """
    Stub: _TypeAlias = _client_0.AsyncStub
    Metadata: _TypeAlias = _models_0.SocketMetadata
    Incoming: _TypeAlias = _models_0.SocketIncoming
    Outgoing: _TypeAlias = _models_0.SocketOutgoing
    State: _TypeAlias = _models_0.SocketState
    EmittedState: _TypeAlias = _models_0.SocketEmittedState
    StatePatch: _TypeAlias = _models_0.SocketStatePatch
    Point: _TypeAlias = _models_0.Point
    TraceEntry: _TypeAlias = _models_0.TraceEntry
    SocketCommand: _TypeAlias = _models_0.SocketCommand
    Viewer: _TypeAlias = _models_0.Viewer
    Action: _TypeAlias = _models_0.Action
    Event: _TypeAlias = _models_0.Event
    Incident: _TypeAlias = _models_0.Incident
    SocketAction: _TypeAlias = _models_0.SocketAction
    SocketEvent: _TypeAlias = _models_0.SocketEvent
    SocketIncident: _TypeAlias = _models_0.SocketIncident
    CameraState: _TypeAlias = _models_0.CameraState
    RelayStart: _TypeAlias = _models_0.RelayStart
    OutboxItem: _TypeAlias = _models_0.OutboxItem
    CameraCore: _TypeAlias = _models_0.CameraCore
    MessageResult: _TypeAlias = _models_0.MessageResult

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
        class message:
            """Apply one ActorMessage (schema.ActorMessage JSON). Idempotent for duplicates."""
            Args: _TypeAlias = _builtins.tuple[_models_0.RpcMethodMessageParameter0]
            Result: _TypeAlias = _models_0.RpcMethodMessageResult

        class pending_relay:
            """Relay calls not yet accepted by the relay, oldest first."""
            Args: _TypeAlias = _builtins.tuple[()]
            Result: _TypeAlias = _models_0.RpcMethodPendingRelayResult

        class relay_delivered:
            """The relay answered 2xx for this outbox item. Returns False if it was not pending."""
            Args: _TypeAlias = _builtins.tuple[_models_0.RpcMethodRelayDeliveredParameter0]
            Result: _TypeAlias = _models_0.RpcMethodRelayDeliveredResult

        class state:
            """Types for Camera.state."""
            Args: _TypeAlias = _builtins.tuple[()]
            Result: _TypeAlias = _models_0.RpcMethodStateResult

from . import _site as _client_1
from . import _site_models as _models_1

class Site:
    """Roll-up of every camera at one site. Cameras push their full state on every change."""
    Stub: _TypeAlias = _client_1.AsyncStub
    Metadata: _TypeAlias = _models_1.SocketMetadata
    Incoming: _TypeAlias = _models_1.SocketIncoming
    Outgoing: _TypeAlias = _models_1.SocketOutgoing
    State: _TypeAlias = _models_1.SocketState
    EmittedState: _TypeAlias = _models_1.SocketEmittedState
    StatePatch: _TypeAlias = _models_1.SocketStatePatch
    TraceEntry: _TypeAlias = _models_1.TraceEntry
    SocketCommand: _TypeAlias = _models_1.SocketCommand
    Viewer: _TypeAlias = _models_1.Viewer
    ActionInput: _TypeAlias = _models_1.ActionInput
    ActionOutput: _TypeAlias = _models_1.ActionOutput
    IncidentInput: _TypeAlias = _models_1.IncidentInput
    IncidentOutput: _TypeAlias = _models_1.IncidentOutput
    Action: _TypeAlias = _models_1.Action
    Incident: _TypeAlias = _models_1.Incident
    CameraStateInput: _TypeAlias = _models_1.CameraStateInput
    CameraStateOutput: _TypeAlias = _models_1.CameraStateOutput
    SiteState: _TypeAlias = _models_1.SiteState
    CameraState: _TypeAlias = _models_1.CameraState
    SocketSiteState: _TypeAlias = _models_1.SocketSiteState

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
        class state:
            """Types for Site.state."""
            Args: _TypeAlias = _builtins.tuple[()]
            Result: _TypeAlias = _models_1.RpcMethodStateResult

        class update_camera:
            """Types for Site.update_camera."""
            Args: _TypeAlias = _builtins.tuple[_models_1.RpcMethodUpdateCameraParameter0]
            Result: _TypeAlias = None

__all__ = ['Camera', 'Site']
