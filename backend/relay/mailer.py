"""Supervisor email through Apple Mail on this Mac (AppleScript), so no mail credentials are needed.
With SUPERVISOR_EMAIL unset, nothing is sent: messages are appended to data/relay_outbox.jsonl."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from .config import CONFIG

log = logging.getLogger("saesr.relay.mail")

_SCRIPT = """on run argv
  tell application "Mail"
    set m to make new outgoing message with properties {subject:(item 1 of argv), content:(item 2 of argv), visible:false}
    tell m to make new to recipient at end of to recipients with properties {address:(item 3 of argv)}
    send m
  end tell
end run"""


async def send(subject: str, body: str) -> dict:
    record = {"at": datetime.now(timezone.utc).isoformat(), "to": CONFIG.supervisor_email or None, "subject": subject, "body": body}
    CONFIG.data_dir.mkdir(parents=True, exist_ok=True)
    with open(CONFIG.data_dir / "relay_outbox.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    if not CONFIG.supervisor_email:
        log.info("email (outbox only, SUPERVISOR_EMAIL unset): %s", subject)
        return {"sent": False, "outbox": True}
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", _SCRIPT, subject, body, CONFIG.supervisor_email,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), 30)
    if proc.returncode != 0:
        log.warning("Mail.app send failed: %s", err.decode()[:200])
        return {"sent": False, "error": err.decode()[:200]}
    log.info("emailed %s: %s", CONFIG.supervisor_email, subject)
    return {"sent": True, "to": CONFIG.supervisor_email}
