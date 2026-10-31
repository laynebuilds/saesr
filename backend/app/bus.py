"""The one interface between the CV pipeline (or mock) and the API.

Producers call publish(event) and set_frame(jpeg). The API streams from subscribe()
and serves latest_frame(). Keep this surface stable: both sides build against it.

Every event is also appended to an on-disk log (EVENTS_LOG, JSON lines) and the log
is replayed on startup, so incidents survive a restart and the weekly summary has
real history to work from.
"""
import asyncio
import json
import logging
import os
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Optional

from .schema import SaesrEvent

log = logging.getLogger("saesr.bus")

RECENT_MAX = 5000
SUBSCRIBER_QUEUE_MAX = 256  # a slow client drops its oldest events instead of growing memory
EVENTS_LOG = Path(os.environ.get("EVENTS_LOG", Path(__file__).resolve().parent.parent / "data" / "events.jsonl"))

_subscribers: set[asyncio.Queue] = set()
_recent: deque[SaesrEvent] = deque(maxlen=RECENT_MAX)
_frame: Optional[bytes] = None
_frame_seq = 0


def load_history() -> int:
    """Replay the event log into memory. Bad lines are skipped, never fatal."""
    if not EVENTS_LOG.exists():
        return 0
    loaded = 0
    with EVENTS_LOG.open() as f:
        for line in f:
            try:
                _recent.append(SaesrEvent.model_validate_json(line))
                loaded += 1
            except Exception:
                continue
    return loaded


def _persist(event: SaesrEvent) -> None:
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_LOG.open("a") as f:
            f.write(event.model_dump_json() + "\n")
    except OSError as err:
        log.warning("could not persist event %s: %s", event.id, err)


async def publish(event: SaesrEvent) -> None:
    _recent.append(event)
    _persist(event)
    for q in list(_subscribers):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(event)


def set_frame(jpeg: bytes) -> None:
    global _frame, _frame_seq
    _frame = jpeg
    _frame_seq += 1


def latest_frame() -> Optional[bytes]:
    return _frame


def frame_seq() -> int:
    """Increments on every new frame, so streamers only send frames that changed."""
    return _frame_seq


def recent(limit: int = 100) -> list[SaesrEvent]:
    return list(_recent)[-limit:]


def open_subscription() -> asyncio.Queue:
    """A bounded queue that receives every new event. Always pair with close_subscription."""
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
    _subscribers.add(q)
    return q


def close_subscription(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


async def subscribe() -> AsyncIterator[SaesrEvent]:
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)
