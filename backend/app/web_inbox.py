"""Footage reviewed on the website becomes a real detection on the floor.

saesr.com's "Review footage" (landing/api/analyze.ts) writes each violation it finds to the
Supabase table web_detections, with its keyframes in Storage. This task polls that table and
publishes every new row on the bus as an Event, so it runs the full pipeline: camera actor
incident, relay agents over Agent Relay, manager call or text by severity, write-through back to
Supabase (which the public demo shows live). Rows are claimed by setting processed_at, so a row
is ingested once. Off unless Supabase is configured; never blocks anything.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import bus
from .schema import Event

log = logging.getLogger("saesr.web_inbox")

POLL_S = 2.0
TYPES = {"unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves"}


def to_event(row: dict) -> Optional[Event]:
    if row.get("type") not in TYPES:
        return None
    return Event(
        camera_id=row.get("camera_id") or "cam-01",
        type=row["type"],
        confidence=float(row.get("confidence") or 0.8),
        zone_id=row.get("zone_id") or "zone-pack-line",
        metrics={"source": "footage review", "reported_severity": row.get("severity"), "summary": row.get("summary")},
        keyframes=list(row.get("keyframes") or [])[:3],
    )


async def run(url: str, key: str, client: Optional[httpx.AsyncClient] = None) -> None:
    h = {"apikey": key, "authorization": f"Bearer {key}"}
    c = client or httpx.AsyncClient(timeout=10)
    started = datetime.now(timezone.utc).isoformat()
    while True:
        try:
            r = await c.get(f"{url}/rest/v1/web_detections",
                            params={"processed_at": "is.null", "created_at": f"gte.{started}", "order": "id.asc", "limit": "5"}, headers=h)
            for row in r.json() if r.status_code < 300 else []:
                claim = await c.patch(f"{url}/rest/v1/web_detections", params={"id": f"eq.{row['id']}", "processed_at": "is.null"},
                                      headers={**h, "prefer": "return=representation", "content-type": "application/json"},
                                      json={"processed_at": datetime.now(timezone.utc).isoformat()})
                if claim.status_code >= 300 or not claim.json():
                    continue  # someone else claimed it
                if row.get("type") == "damaged_packaging":
                    await _packaging_alert(row)
                    continue
                event = to_event(row)
                if event:
                    await bus.publish(event)
                    await c.patch(f"{url}/rest/v1/web_detections", params={"id": f"eq.{row['id']}"},
                                  headers={**h, "content-type": "application/json"}, json={"event_id": event.event_id})
                    log.info("web detection %s -> %s %s", row["id"], event.type, event.event_id)
        except (httpx.HTTPError, ValueError, KeyError) as err:
            log.warning("web inbox poll failed: %s", err)
        await asyncio.sleep(POLL_S)


async def _packaging_alert(row: dict) -> None:
    """Damaged or open packaging is not an incident type of the CV pipeline, so the manager is phoned
    directly with the same short briefing (falls back to a log line if calling is not configured)."""
    from . import voice_call
    from .photon_alert import _env

    phone = _env("MANAGER_PHONE")
    summary = (row.get("summary") or "a box or wrapping is damaged and could drop its load").rstrip(".")
    text = (f"saesr safety alert. Worker number {_env('WORKER_NUMBER') or '1'}. Name: {_env('WORKER_NAME') or 'unknown'}. "
            f"Issue detected: damaged packaging. {summary}. Action to be taken: stop handling it, re-wrap or replace the box "
            "before it is moved. Goodbye.")
    if not phone or (_env("CALL_MANAGER") or _env("PHOTON_CALL")) != "1":
        log.info("packaging hazard (no call configured): %s", summary)
        return
    try:
        log.info("packaging hazard, calling the manager: %s", await voice_call.call(phone, text))
    except Exception as err:
        log.warning("packaging call failed: %s", err)


def start() -> Optional[asyncio.Task]:
    url, key = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SECRET_KEY", "")
    if not (url and key):
        return None
    log.info("web inbox on: footage reviewed on the website enters the pipeline")
    return asyncio.create_task(run(url, key))
