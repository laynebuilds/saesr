"""Write-through of saesr state to Supabase Postgres (contract v2, supabase/schema_v2.sql).

Two inputs, one ordered outbox:
  - every bus event                  -> events
  - every CameraState from the actors -> incidents, actions, relay_trace,
                                         corrective_records (open_corrective_record done),
                                         batch_holds (flag_batch_hold done)

Enabled when SUPABASE_URL and SUPABASE_SECRET_KEY are set. Log-only: detection and the actors
never wait on it. Writes queue in a bounded buffer (oldest dropped when full), are delivered in
order with backoff, a write Supabase keeps rejecting (4xx) is dropped after GIVE_UP_AFTER tries,
and every write is an idempotent PostgREST upsert, so retries and repeated states never duplicate.
Media is uploaded by the CV worker (app/media.py), not here.
"""
import asyncio
import hashlib
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import actors, bus
from .schema import SITE_ID, Event

log = logging.getLogger("saesr.supabase")

BUFFER_MAX = 2000
BACKOFF_S = (1, 2, 5, 10, 30)
GIVE_UP_AFTER = 5  # attempts for a write Supabase keeps rejecting (4xx), so it cannot block the queue
HOLD_WINDOW_S = 15 * 60  # default batch hold window around the incident when params do not give one

# table -> on_conflict columns when the key is not the primary key (None = primary key)
ON_CONFLICT: dict[str, Optional[str]] = {
    "events": None, "incidents": None, "actions": None, "corrective_records": None, "batch_holds": None,
    "relay_trace": "incident_id,agent,started_at",
}
IGNORE_DUPLICATES = {"relay_trace"}  # trace entries are append-only


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


class Rejected(Exception):
    """Supabase refused the write (4xx); retrying the same payload will not help."""


def derived_id(prefix: str, action_id: str) -> str:
    """Stable id for a row derived from an action, so re-sent states upsert the same row."""
    return f"{prefix}_{hashlib.sha1(action_id.encode()).hexdigest()[:16]}"


def _shift(ts: str, seconds: float) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


# ---- row mappers (pure) -----------------------------------------------------------------------


def event_row(e: Event) -> dict:
    return {
        "event_id": e.event_id,
        "site_id": e.site_id,
        "camera_id": e.camera_id,
        "type": e.type,
        "confidence": e.confidence,
        "ts": e.timestamp,
        "zone_id": e.zone_id,
        "centroid_x": e.centroid.x if e.centroid else None,
        "centroid_y": e.centroid.y if e.centroid else None,
        "metrics": e.metrics,
        "keyframes": e.keyframes,
        "clip_url": e.clip_url,
    }


def state_rows(state: dict) -> list[tuple[str, dict]]:
    """(table, row) writes for one CameraState dict, parents before children."""
    site_id = state.get("site_id") or SITE_ID
    out: list[tuple[str, dict]] = []
    for inc in state.get("incidents") or []:
        relay = inc.get("relay") or {}
        incident_id = inc["incident_id"]
        out.append(("incidents", {
            "id": incident_id,
            "site_id": site_id,
            "camera_id": inc.get("camera_id") or state.get("camera_id"),
            "type": inc.get("type"),
            "zone_id": inc.get("zone_id"),
            "status": inc.get("status"),
            "severity": inc.get("severity"),
            "opened_at": inc.get("opened_at"),
            "updated_at": inc.get("updated_at"),
            "closed_at": inc.get("closed_at"),
            "triage": relay.get("triage"),
            "coach": relay.get("coach"),
            "relay": relay,
            "low_confidence": bool(inc.get("low_confidence")),
            "keyframes": inc.get("keyframes") or [],
            "clip_url": inc.get("clip_url"),
        }))
        for t in inc.get("trace") or []:
            if not t.get("agent") or not t.get("started_at"):
                continue
            out.append(("relay_trace", {
                "incident_id": incident_id,
                "agent": t["agent"],
                "model": t.get("model"),
                "started_at": t["started_at"],
                "ended_at": t.get("ended_at"),
                "output_summary": t.get("output_summary"),
                "error": t.get("error"),
            }))
        for a in inc.get("actions") or []:
            out.append(("actions", {
                "action_id": a["action_id"],
                "incident_id": incident_id,
                "kind": a.get("kind"),
                "status": a.get("status"),
                "params": a.get("params") or {},
                "approved_by": a.get("approved_by"),
                "executed_at": a.get("executed_at"),
                "updated_at": inc.get("updated_at"),
            }))
            if a.get("status") != "done":
                continue
            params = a.get("params") or {}
            if a.get("kind") == "open_corrective_record":
                out.append(("corrective_records", {
                    "id": derived_id("cr", a["action_id"]),
                    "incident_id": incident_id,
                    "description": params.get("description")
                    or f"{inc.get('type', 'incident')} in {inc.get('zone_id', 'unknown zone')}",
                    "standard_tag": params.get("standard_tag"),
                    "created_at": a.get("executed_at") or inc.get("updated_at"),
                }))
            elif a.get("kind") == "flag_batch_hold":
                anchor = inc.get("opened_at") or a.get("executed_at")
                out.append(("batch_holds", {
                    "id": derived_id("bh", a["action_id"]),
                    "incident_id": incident_id,
                    "camera_id": inc.get("camera_id") or state.get("camera_id"),
                    "window_start": params.get("window_start") or (_shift(anchor, -HOLD_WINDOW_S) if anchor else None),
                    "window_end": params.get("window_end") or (_shift(anchor, HOLD_WINDOW_S) if anchor else None),
                    "status": params.get("hold_status") or "held",
                }))
    return out


# ---- delivery ---------------------------------------------------------------------------------


class SupabaseSink:
    def __init__(self, url: str, key: str, client: Optional[httpx.AsyncClient] = None, backoff=BACKOFF_S):
        self.url = url.rstrip("/")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.client = client or httpx.AsyncClient(timeout=20)
        self.backoff = backoff
        self.buffer: deque[tuple[str, dict]] = deque(maxlen=BUFFER_MAX)
        self.wake = asyncio.Event()
        self.sent = 0
        self.failed = 0
        self.dropped_overflow = 0
        self._last: dict[tuple[str, str], dict] = {}  # (table, key) -> last row written, skips no-op rewrites

    def enqueue(self, table: str, row: dict) -> None:
        key = (table, str(row.get("id") or row.get("event_id") or row.get("action_id")
                          or (row.get("incident_id"), row.get("agent"), row.get("started_at"))))
        if self._last.get(key) == row:
            return  # unchanged since we last queued it (states repeat every mutation)
        self._last[key] = row
        if len(self._last) > BUFFER_MAX * 5:
            self._last.clear()
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped_overflow += 1
        self.buffer.append((table, row))
        self.wake.set()

    def add_event(self, event: Event) -> None:
        self.enqueue("events", event_row(event))

    def add_state(self, state: dict) -> None:
        for table, row in state_rows(state):
            self.enqueue(table, row)

    async def write(self, table: str, row: dict) -> None:
        params = {}
        if ON_CONFLICT.get(table):
            params["on_conflict"] = ON_CONFLICT[table]
        resolution = "ignore-duplicates" if table in IGNORE_DUPLICATES else "merge-duplicates"
        r = await self.client.post(
            f"{self.url}/rest/v1/{table}",
            params=params,
            headers={**self.headers, "content-type": "application/json",
                     "Prefer": f"resolution={resolution},return=minimal"},
            json=row,
        )
        self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code >= 500:
            raise httpx.HTTPStatusError(f"{r.status_code} {r.text[:200]}", request=r.request, response=r)
        if r.status_code >= 400:
            raise Rejected(f"{r.status_code} {r.text[:200]}")

    def _delay(self, attempt: int) -> float:
        return self.backoff[min(attempt, len(self.backoff) - 1)]

    async def drain(self) -> None:
        """Deliver buffered writes in order; retry the head with backoff."""
        attempt = 0
        while self.buffer:
            table, row = self.buffer[0]
            try:
                await self.write(table, row)
                self.buffer.popleft()
                self.sent += 1
                attempt = 0
            except Rejected as err:
                attempt += 1
                if attempt >= GIVE_UP_AFTER:
                    log.error("dropping %s write after %d rejections: %s", table, attempt, err)
                    self.buffer.popleft()
                    self.failed += 1
                    attempt = 0
                    continue
                await asyncio.sleep(self._delay(attempt - 1))
            except (httpx.HTTPError, OSError) as err:
                log.warning("supabase unreachable (%s); %d buffered", err, len(self.buffer))
                await asyncio.sleep(self._delay(attempt))
                attempt += 1

    async def deliver_loop(self) -> None:
        while True:
            if not self.buffer:
                self.wake.clear()
                await self.wake.wait()
            await self.drain()

    async def pump_events(self) -> None:
        q = bus.open_subscription()
        try:
            while True:
                self.add_event(await q.get())
        finally:
            bus.close_subscription(q)

    async def pump_states(self) -> None:
        try:
            if not actors.enabled():
                return
            q = actors.open_state_subscription()
        except Exception:
            log.exception("actors state subscription unavailable; incidents will not be mirrored")
            return
        try:
            while True:
                state = await q.get()
                try:
                    self.add_state(state if isinstance(state, dict) else state.model_dump(mode="json"))
                except Exception:
                    log.exception("could not map camera state to rows")
        finally:
            actors.close_state_subscription(q)

    def status(self) -> dict:
        return {"enabled": True, "sent": self.sent, "failed": self.failed, "buffered": len(self.buffer),
                "dropped_overflow": self.dropped_overflow}

    async def run(self) -> None:
        try:
            await asyncio.gather(self.deliver_loop(), self.pump_events(), self.pump_states())
        finally:
            await self.client.aclose()


current: Optional[SupabaseSink] = None


def start() -> Optional[asyncio.Task]:
    global current
    if not enabled():
        return None
    current = SupabaseSink(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    log.info("supabase sink on")
    return asyncio.create_task(current.run())
