"""Wire formats for what agents say to each other on Relay.

Every message is readable by a human first (it shows up in the channel as-is) and carries a
machine line last, so agents never have to guess:

    INCIDENT HIGH unsafe_lift | zone loading-dock | cam-1-loading-dock | 18:45:27Z
    back bent 62 degrees with straight legs while lifting (confidence 0.91)
    saesr:event {"id": "evt_...", "camera": "...", ...}

Acknowledgements are plain text so a person on the CLI (or a Claude Code session) can type them:
"ack evt_20261031_184527_1_0007" or "ack all".
"""
import json
import re
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from ..schema import SaesrEvent

EVENT_PREFIX = "saesr:event "
ACTION_PREFIX = "saesr:action "
_ACK = re.compile(r"^\s*(?:ack|acknowledged?)\s+(all|evt_[A-Za-z0-9_\-]+(?:[\s,]+evt_[A-Za-z0-9_\-]+)*)", re.I)


def _clock(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%H:%M:%SZ")
    except ValueError:
        return ts


def incident_text(event: SaesrEvent) -> str:
    head = f"INCIDENT {event.severity.upper()} {event.type} | zone {event.zone} | {event.camera} | {_clock(event.timestamp)}"
    detail = event.detail or event.type.replace("_", " ")
    if event.confidence is not None:
        detail += f" (confidence {event.confidence:.2f})"
    return f"{head}\n{detail}\n{EVENT_PREFIX}{event.model_dump_json()}"


def parse_incident(text: str) -> Optional[SaesrEvent]:
    for line in reversed(text.splitlines()):
        if line.startswith(EVENT_PREFIX):
            try:
                return SaesrEvent.model_validate_json(line[len(EVENT_PREFIX):])
            except ValidationError:
                return None
    return None


def parse_ack(text: str) -> Optional[list[str]]:
    """["all"] or the list of event ids acknowledged, else None."""
    m = _ACK.match(text)
    if not m:
        return None
    body = m.group(1)
    return ["all"] if body.lower() == "all" else re.findall(r"evt_[A-Za-z0-9_\-]+", body)


def action_line(action: dict) -> str:
    """Machine line appended to supervisor messages so other agents can act on them."""
    return ACTION_PREFIX + json.dumps(action, separators=(",", ":"))


def parse_action(text: str) -> Optional[dict]:
    for line in reversed(text.splitlines()):
        if line.startswith(ACTION_PREFIX):
            try:
                return json.loads(line[len(ACTION_PREFIX):])
            except ValueError:
                return None
    return None
