"""The one interface between the CV pipeline (or mock) and the API.

Producers call publish(event) and set_frame(jpeg). The API streams from subscribe()
and serves latest_frame(). Keep this surface stable: both sides build against it.
"""
import asyncio
from collections import deque
from typing import AsyncIterator, Optional

from .schema import SaesrEvent

_subscribers: set[asyncio.Queue] = set()
_recent: deque[SaesrEvent] = deque(maxlen=500)
_frame: Optional[bytes] = None


async def publish(event: SaesrEvent) -> None:
    _recent.append(event)
    for q in list(_subscribers):
        q.put_nowait(event)


def set_frame(jpeg: bytes) -> None:
    global _frame
    _frame = jpeg


def latest_frame() -> Optional[bytes]:
    return _frame


def recent(limit: int = 100) -> list[SaesrEvent]:
    return list(_recent)[-limit:]


async def subscribe() -> AsyncIterator[SaesrEvent]:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)
