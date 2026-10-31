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
    """One camera feed. Owns its liveness and forwards incidents to its Site.
    
    A durable alarm marks the camera offline when heartbeats stop, even if nothing else
    ever calls it again (the runtime wakes the actor for on_alarm). Online/offline flips
    are pushed to the Site so the dashboard snapshot shows camera health live.
    """
    def __init__(self, actor_id: str, transport: _ActorTransport | None = None) -> None:
        """Address an actor. An injected transport remains caller-owned."""
        self._actor_id = actor_id
        self._transport = transport if transport is not None else _default_client()

    def get_status(_self) -> _models.RpcMethodGetStatusResult:
        """Invoke Camera.get_status synchronously.
        
        Arguments and results are validated against the actor contract.
        Remote failures raise ActorInvocationError.
        """
        _args = _arguments([], [])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'get_status', _args)
        _adapter: _TypeAdapter[_models.RpcMethodGetStatusResult] = _TypeAdapter(_models.RpcMethodGetStatusResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def heartbeat(_self, fps: _models.RpcMethodHeartbeatParameter0 | _Unset = _UNSET, site: _models.RpcMethodHeartbeatParameter1 | _Unset = _UNSET) -> _models.RpcMethodHeartbeatResult:
        """The camera is alive and producing frames at `fps`."""
        _args = _arguments([_argument(fps, _TypeAdapter(_models.RpcMethodHeartbeatParameter0)), _argument(site, _TypeAdapter(_models.RpcMethodHeartbeatParameter1))], [_json.loads("null"), _json.loads("\"demo-warehouse\"")])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'heartbeat', _args)
        _adapter: _TypeAdapter[_models.RpcMethodHeartbeatResult] = _TypeAdapter(_models.RpcMethodHeartbeatResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    def record(_self, event: _models.RpcMethodRecordParameter0, site: _models.RpcMethodRecordParameter1 | _Unset = _UNSET) -> _models.RpcMethodRecordResult:
        """Record an incident seen by this camera and forward it to the Site.
        
        If the Site call fails, this call raises and its own changes roll back, so the caller
        retries the whole thing; Site.record is idempotent by event id, so a retry after a
        partial success is harmless (at-least-once delivery, exactly-once effect).
        """
        _args = _arguments([_argument(event, _TypeAdapter(_models.RpcMethodRecordParameter0)), _argument(site, _TypeAdapter(_models.RpcMethodRecordParameter1))], [_UNSET, _json.loads("\"demo-warehouse\"")])
        _result = _self._transport.invoke('Camera', _self._actor_id, 'record', _args)
        _adapter: _TypeAdapter[_models.RpcMethodRecordResult] = _TypeAdapter(_models.RpcMethodRecordResult)
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
    """One camera feed. Owns its liveness and forwards incidents to its Site.
    
    A durable alarm marks the camera offline when heartbeats stop, even if nothing else
    ever calls it again (the runtime wakes the actor for on_alarm). Online/offline flips
    are pushed to the Site so the dashboard snapshot shows camera health live.
    """
    def __init__(self, actor_id: str, transport: _AsyncActorTransport | None = None) -> None:
        """Address an actor. An injected transport remains caller-owned."""
        self._actor_id = actor_id
        self._transport = transport if transport is not None else _default_async_client()

    async def get_status(_self) -> _models.RpcMethodGetStatusResult:
        """Invoke Camera.get_status asynchronously.
        
        Arguments and results are validated against the actor contract.
        Remote failures raise ActorInvocationError.
        """
        _args = _arguments([], [])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'get_status', _args)
        _adapter: _TypeAdapter[_models.RpcMethodGetStatusResult] = _TypeAdapter(_models.RpcMethodGetStatusResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def heartbeat(_self, fps: _models.RpcMethodHeartbeatParameter0 | _Unset = _UNSET, site: _models.RpcMethodHeartbeatParameter1 | _Unset = _UNSET) -> _models.RpcMethodHeartbeatResult:
        """The camera is alive and producing frames at `fps`."""
        _args = _arguments([_argument(fps, _TypeAdapter(_models.RpcMethodHeartbeatParameter0)), _argument(site, _TypeAdapter(_models.RpcMethodHeartbeatParameter1))], [_json.loads("null"), _json.loads("\"demo-warehouse\"")])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'heartbeat', _args)
        _adapter: _TypeAdapter[_models.RpcMethodHeartbeatResult] = _TypeAdapter(_models.RpcMethodHeartbeatResult)
        return _adapter.validate_json(_json.dumps(_result), strict=True)

    async def record(_self, event: _models.RpcMethodRecordParameter0, site: _models.RpcMethodRecordParameter1 | _Unset = _UNSET) -> _models.RpcMethodRecordResult:
        """Record an incident seen by this camera and forward it to the Site.
        
        If the Site call fails, this call raises and its own changes roll back, so the caller
        retries the whole thing; Site.record is idempotent by event id, so a retry after a
        partial success is harmless (at-least-once delivery, exactly-once effect).
        """
        _args = _arguments([_argument(event, _TypeAdapter(_models.RpcMethodRecordParameter0)), _argument(site, _TypeAdapter(_models.RpcMethodRecordParameter1))], [_UNSET, _json.loads("\"demo-warehouse\"")])
        _result = await _self._transport.invoke('Camera', _self._actor_id, 'record', _args)
        _adapter: _TypeAdapter[_models.RpcMethodRecordResult] = _TypeAdapter(_models.RpcMethodRecordResult)
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
