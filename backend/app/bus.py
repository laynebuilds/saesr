"""The one interface between the CV pipeline (or mock) and the API.

Producers call publish(event) and set_frame(jpeg, camera_id). The API streams from subscribe()
and serves latest_frame(camera_id). Keep this surface stable: both sides build against it.

Events are schema.Event (contract v2). publish() drops exact event_id duplicates it has seen
recently (the camera actor dedupes too; this just keeps the stream and the log clean).

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

from .schema import Event

log = logging.getLogger("saesr.bus")

RECENT_MAX = 5000
SUBSCRIBER_QUEUE_MAX = 256  # a slow client drops its oldest events instead of growing memory
EVENTS_LOG = Path(os.environ.get("EVENTS_LOG", Path(__file__).resolve().parent.parent / "data" / "events.jsonl"))

_subscribers: set[asyncio.Queue] = set()
_recent: deque[Event] = deque(maxlen=RECENT_MAX)
_seen_ids: dict[str, None] = {}  # insertion-ordered set of recent event ids, bounded to RECENT_MAX
_frames: dict[str, bytes] = {}  # camera_id -> latest annotated jpeg
_frame_seqs: dict[str, int] = {}  # camera_id -> frame counter
_last_camera: Optional[str] = None
_frame_seq = 0  # global counter across all cameras


def _remember(event_id: str) -> bool:
    """True if this id is new (and remembers it), False for a duplicate."""
    if event_id in _seen_ids:
        return False
    _seen_ids[event_id] = None
    while len(_seen_ids) > RECENT_MAX:
        _seen_ids.pop(next(iter(_seen_ids)))
    return True


def load_history() -> int:
    """Replay the event log into memory. Bad or old-format (v1) lines are skipped, never fatal."""
    if not EVENTS_LOG.exists():
        return 0
    loaded = 0
    with EVENTS_LOG.open() as f:
        for line in f:
            try:
                event = Event.model_validate_json(line)
            except Exception:
                continue
            if _remember(event.event_id):
                _recent.append(event)
                loaded += 1
    return loaded


def _persist(event: Event) -> None:
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_LOG.open("a") as f:
            f.write(event.model_dump_json() + "\n")
    except OSError as err:
        log.warning("could not persist event %s: %s", event.event_id, err)


async def publish(event: Event) -> bool:
    """Append, persist and fan out. Returns False (and does nothing) for a duplicate event_id."""
    if not _remember(event.event_id):
        return False
    _recent.append(event)
    _persist(event)
    for q in list(_subscribers):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(event)
    return True


def set_frame(jpeg: bytes, camera_id: str = "cam-01") -> None:
    global _last_camera, _frame_seq
    _frames[camera_id] = jpeg
    _frame_seqs[camera_id] = _frame_seqs.get(camera_id, 0) + 1
    _last_camera = camera_id
    _frame_seq += 1


def latest_frame(camera_id: Optional[str] = None) -> Optional[bytes]:
    """Latest frame of that camera; None = the most recent frame of any camera."""
    if camera_id is None:
        return _frames.get(_last_camera) if _last_camera else None
    return _frames.get(camera_id)


def frame_seq(camera_id: Optional[str] = None) -> int:
    """Increments on every new frame (of that camera, or of any camera for None),
    so streamers only send frames that changed."""
    if camera_id is None:
        return _frame_seq
    return _frame_seqs.get(camera_id, 0)


def cameras_with_frames() -> list[str]:
    return sorted(_frames)


def reset() -> None:
    """Clear in-memory state (tests)."""
    global _last_camera, _frame_seq
    _recent.clear()
    _seen_ids.clear()
    _subscribers.clear()
    _frames.clear()
    _frame_seqs.clear()
    _last_camera = None
    _frame_seq = 0


def recent(limit: int = 100) -> list[Event]:
    return list(_recent)[-limit:]


def open_subscription() -> asyncio.Queue:
    """A bounded queue that receives every new event. Always pair with close_subscription."""
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
    _subscribers.add(q)
    return q


def close_subscription(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


async def subscribe() -> AsyncIterator[Event]:
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)
