"""What the agents said and did, for the dashboard: GET /agents/activity and GET /agents/stream.

Two kinds of items share one feed, newest last:

    message  a Relay message an agent sent or the supervisor received (channel post or DM)
    action   a structured supervisor decision (escalate, pattern, brief, ack) with its evidence

Same back-pressure rules as app.bus: bounded queues, a slow client drops its oldest items.
"""
import asyncio
import itertools
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

RECENT_MAX = 500
SUBSCRIBER_QUEUE_MAX = 256

_recent: deque[dict] = deque(maxlen=RECENT_MAX)
_subscribers: set[asyncio.Queue] = set()
_seq = itertools.count(1)
status: dict[str, Any] = {"enabled": False, "connected": False, "relay_url": None, "channel": None, "agents": [], "error": None}


def _push(item: dict) -> dict:
    _recent.append(item)
    for q in list(_subscribers):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(item)
    return item


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_message(sender: str, to: str, text: str, event_id: Optional[str] = None, relay_id: Optional[str] = None) -> dict:
    """to is "#channel" for a channel post or an agent name for a DM."""
    return _push({
        "id": f"act_{next(_seq):06d}",
        "ts": _now(),
        "kind": "message",
        "agent": sender,
        "to": to,
        "text": text,
        "event_id": event_id,
        "relay_id": relay_id,
        "action": None,
    })


def record_action(agent: str, to: str, text: str, action: dict) -> dict:
    return _push({
        "id": f"act_{next(_seq):06d}",
        "ts": _now(),
        "kind": "action",
        "agent": agent,
        "to": to,
        "text": text,
        "event_id": (action.get("event_ids") or [None])[0],
        "relay_id": None,
        "action": action,
    })


def recent(limit: int = 100, kind: Optional[str] = None) -> list[dict]:
    items = [i for i in _recent if kind is None or i["kind"] == kind]
    return items[-limit:]


def open_subscription() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
    _subscribers.add(q)
    return q


def close_subscription(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def reset() -> None:
    _recent.clear()
    _subscribers.clear()
