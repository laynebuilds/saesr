from __future__ import annotations
import json as _json
from collections.abc import Callable as _Callable, Awaitable as _Awaitable
from pydantic import TypeAdapter as _TypeAdapter
from durable_actors.client import ActorTransport as _ActorTransport, SocketGrant as _SocketGrant, default_client as _default_client
from durable_actors.async_client import AsyncActorTransport as _AsyncActorTransport, default_async_client as _default_async_client
from durable_actors.async_connection import AsyncConnection as _AsyncConnection
from durable_actors.connection import Connection as _Connection
from durable_actors.subscription import Subscription as _Subscription, AsyncSubscription as _AsyncSubscription
from durable_actors.generated import UNSET as _UNSET, Unset as _Unset, arguments as _arguments, argument as _argument
from . import _camera_models as _models

class Stub:
    """One camera. Owns its incidents, the relay outbox and the verification windows.
    
    Serialized execution means a detection, a relay update and an approval for this camera never
    interleave. A single durable alarm is always set to the earliest pending deadline (offline
    timeout, end of recovering, verification window), so time-driven transitions happen even if
    nothing else ever calls the actor.
    """
    def __init__(self, actor_id: str, transport: _ActorTransport | None = None) -> None:
        """Address an actor. An injected transport remains caller-owned."""
        self._actor_id = actor_id
        self._transport = transport if transport is not None else _default_client()

    def message(_self, msg: _models.RpcMethodMessageParameter0) -> _models.RpcMethodMessageResult:
        """Apply one ActorMessage (schema.ActorMessage JSON). Idempotent for duplicates."""
        _args = _arguments([_argument(msg, _TypeAdapter(_models.RpcMethodMessageParameter0))], [_UNSET])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'message', _args)
        _adapter: _TypeAdapter[_models.RpcMethodMessageResult] = _TypeAdapter(_models.RpcMethodMessageResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def pending_relay(_self) -> _models.RpcMethodPendingRelayResult:
        """Relay calls not yet accepted by the relay, oldest first."""
        _args = _arguments([], [])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'pending_relay', _args)
        _adapter: _TypeAdapter[_models.RpcMethodPendingRelayResult] = _TypeAdapter(_models.RpcMethodPendingRelayResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def relay_delivered(_self, outbox_id: _models.RpcMethodRelayDeliveredParameter0) -> _models.RpcMethodRelayDeliveredResult:
        """The relay answered 2xx for this outbox item. Returns False if it was not pending."""
        _args = _arguments([_argument(outbox_id, _TypeAdapter(_models.RpcMethodRelayDeliveredParameter0))], [_UNSET])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'relay_delivered', _args)
        _adapter: _TypeAdapter[_models.RpcMethodRelayDeliveredResult] = _TypeAdapter(_models.RpcMethodRelayDeliveredResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def state(_self) -> _models.RpcMethodStateResult:
        """Invoke Camera.state synchronously.
        
        Arguments and results are validated against the actor contract.
        Remote failures raise ActorInvocationError.
        """
        _args = _arguments([], [])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'state', _args)
        _adapter: _TypeAdapter[_models.RpcMethodStateResult] = _TypeAdapter(_models.RpcMethodStateResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def broadcast(self, message: _models.SocketOutgoing) -> None:
        """Broadcast a typed application message to every connection without persisting it."""
        self._transport.broadcast('Camera', self._actor_id, _argument(message, _TypeAdapter(_models.SocketOutgoing)))

    def prepare_websocket(self, metadata: _models.SocketMetadata) -> _SocketGrant:
        """Authorize a WebSocket connection without opening it.
        
        Args:
            metadata: Typed metadata passed to the actor's on_connect hook.
        
        Returns:
            A grant containing the connection URL and admission deadline.
            Accepted connections remain authorized until they close.
        """
        return self._transport.prepare_websocket('Camera', self._actor_id, _argument(metadata, _TypeAdapter(_models.SocketMetadata)))

    def connect(self, metadata: _models.SocketMetadata) -> _Connection[_models.SocketIncoming, _models.SocketOutgoing, _models.SocketEmittedState, _models.SocketStatePatch]:
        """Open a typed, synchronous WebSocket connection.
        
        Args:
            metadata: Typed metadata passed to the actor's on_connect hook.
        
        Use send() for application messages and receive() or iteration for
        messages and state events. Use close() or with for cleanup.
        For complete emitted-state callbacks, use subscribe() when available.
        """
        grant = self.prepare_websocket(metadata)
        return _Connection[_models.SocketIncoming, _models.SocketOutgoing, _models.SocketEmittedState, _models.SocketStatePatch].open(grant, _TypeAdapter(_models.SocketIncoming), _TypeAdapter(_models.SocketOutgoing), _TypeAdapter(_models.SocketEmittedState), _TypeAdapter(_models.SocketStatePatch))

class AsyncStub:
    """One camera. Owns its incidents, the relay outbox and the verification windows.
    
    Serialized execution means a detection, a relay update and an approval for this camera never
    interleave. A single durable alarm is always set to the earliest pending deadline (offline
    timeout, end of recovering, verification window), so time-driven transitions happen even if
    nothing else ever calls the actor.
    """
    def __init__(self, actor_id: str, transport: _AsyncActorTransport | None = None) -> None:
        """Address an actor. An injected transport remains caller-owned."""
        self._actor_id = actor_id
        self._transport = transport if transport is not None else _default_async_client()

    async def message(_self, msg: _models.RpcMethodMessageParameter0) -> _models.RpcMethodMessageResult:
        """Apply one ActorMessage (schema.ActorMessage JSON). Idempotent for duplicates."""
        _args = _arguments([_argument(msg, _TypeAdapter(_models.RpcMethodMessageParameter0))], [_UNSET])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'message', _args)
        _adapter: _TypeAdapter[_models.RpcMethodMessageResult] = _TypeAdapter(_models.RpcMethodMessageResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def pending_relay(_self) -> _models.RpcMethodPendingRelayResult:
        """Relay calls not yet accepted by the relay, oldest first."""
        _args = _arguments([], [])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'pending_relay', _args)
        _adapter: _TypeAdapter[_models.RpcMethodPendingRelayResult] = _TypeAdapter(_models.RpcMethodPendingRelayResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def relay_delivered(_self, outbox_id: _models.RpcMethodRelayDeliveredParameter0) -> _models.RpcMethodRelayDeliveredResult:
        """The relay answered 2xx for this outbox item. Returns False if it was not pending."""
        _args = _arguments([_argument(outbox_id, _TypeAdapter(_models.RpcMethodRelayDeliveredParameter0))], [_UNSET])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'relay_delivered', _args)
        _adapter: _TypeAdapter[_models.RpcMethodRelayDeliveredResult] = _TypeAdapter(_models.RpcMethodRelayDeliveredResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def state(_self) -> _models.RpcMethodStateResult:
        """Invoke Camera.state asynchronously.
        
        Arguments and results are validated against the actor contract.
        Remote failures raise ActorInvocationError.
        """
        _args = _arguments([], [])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'state', _args)
        _adapter: _TypeAdapter[_models.RpcMethodStateResult] = _TypeAdapter(_models.RpcMethodStateResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def broadcast(self, message: _models.SocketOutgoing) -> None:
        """Broadcast a typed application message to every connection without persisting it."""
        await self._transport.broadcast('Camera', self._actor_id, _argument(message, _TypeAdapter(_models.SocketOutgoing)))

    async def prepare_websocket(self, metadata: _models.SocketMetadata) -> _SocketGrant:
        """Authorize a WebSocket connection without opening it.
        
        Args:
            metadata: Typed metadata passed to the actor's on_connect hook.
        
        Returns:
            A grant containing the connection URL and admission deadline.
            Accepted connections remain authorized until they close.
        """
        return await self._transport.prepare_websocket('Camera', self._actor_id, _argument(metadata, _TypeAdapter(_models.SocketMetadata)))

    async def connect(self, metadata: _models.SocketMetadata) -> _AsyncConnection[_models.SocketIncoming, _models.SocketOutgoing, _models.SocketEmittedState, _models.SocketStatePatch]:
        """Open a typed, async WebSocket connection.
        
        Args:
            metadata: Typed metadata passed to the actor's on_connect hook.
        
        Use send() for application messages and receive() or iteration for
        messages and state events. Use aclose() or async with for cleanup.
        For complete emitted-state callbacks, use subscribe() when available.
        """
        grant = await self.prepare_websocket(metadata)
        return await _AsyncConnection[_models.SocketIncoming, _models.SocketOutgoing, _models.SocketEmittedState, _models.SocketStatePatch].open(grant, _TypeAdapter(_models.SocketIncoming), _TypeAdapter(_models.SocketOutgoing), _TypeAdapter(_models.SocketEmittedState), _TypeAdapter(_models.SocketStatePatch))
