"""Generated actor clients, authorization types, and browser access helpers."""
from . import actors as actors
from . import async_actors as async_actors
from typing import TypeAlias as _TypeAlias, Never as _Never
from pydantic import TypeAdapter as _TypeAdapter
from durable_actors.client import ActorTransport as _ActorTransport, SocketGrant as _SocketGrant
from durable_actors.proxy import prepare_authorization as _prepare_authorization, prepare_async_authorization as _prepare_async_authorization
from durable_actors.async_client import AsyncActorTransport as _AsyncActorTransport
from durable_actors import ActorSession as ActorSession, ActorSessionTransport as ActorSessionTransport, ActorSessionRejectedError as ActorSessionRejectedError

ActorAuthorization: _TypeAlias = actors.Camera.Authorization | actors.Site.Authorization

class ActorProxy:
    """Issue WebSocket grants for the actors in this generated package."""

    @staticmethod
    def handle(authorization: ActorAuthorization, transport: _ActorTransport | None = None) -> _SocketGrant:
        """Issue a grant after the backend has authenticated and authorized the user."""
        return _prepare_authorization(_TypeAdapter(ActorAuthorization).validate_python(authorization), transport)

class AsyncActorProxy:
    """Issue WebSocket grants with an async transport."""

    @staticmethod
    async def handle(authorization: ActorAuthorization, transport: _AsyncActorTransport | None = None) -> _SocketGrant:
        return await _prepare_async_authorization(_TypeAdapter(ActorAuthorization).validate_python(authorization), transport)
