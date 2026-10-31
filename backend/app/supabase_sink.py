"""Persist every incident to Supabase: the row in Postgres, the clip and still in Storage.

Runs as one bus subscriber, enabled when SUPABASE_URL and SUPABASE_SECRET_KEY are set.
Detection never waits on it: events queue in a bounded buffer, are delivered in order
with backoff, and the API keeps running if Supabase is unreachable. Writes are
idempotent (upsert on id, upsert on storage paths), so retries never duplicate.
"""
import asyncio
import logging
import os
from collections import deque
from pathlib import Path
from typing import Optional

import httpx

from . import bus
from .schema import SaesrEvent

log = logging.getLogger("saesr.supabase")

SITE = os.environ.get("SITE_ID", "demo-warehouse")
BUCKET = "clips"
BUFFER_MAX = 1000
BACKOFF_S = (1, 2, 5, 10, 30)
GIVE_UP_AFTER = 5  # attempts for an event Supabase keeps rejecting (4xx), so it cannot block the queue
CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips"


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


class Rejected(Exception):
    """Supabase refused the write (4xx); retrying the same payload will not help."""


class SupabaseSink:
    def __init__(self, url: str, key: str, client: Optional[httpx.AsyncClient] = None):
        self.url = url.rstrip("/")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.client = client or httpx.AsyncClient(timeout=20)
        self.buffer: deque[SaesrEvent] = deque(maxlen=BUFFER_MAX)
        self.sent = 0
        self.failed = 0

    def public_url(self, path: str) -> str:
        return f"{self.url}/storage/v1/object/public/{BUCKET}/{path}"

    async def upload(self, local_url: Optional[str]) -> Optional[str]:
        """Upload a /clips/<file> produced by the CV pipeline; return its public URL.
        Anything else (sample media, already-remote URLs) is passed through unchanged."""
        if not local_url or not local_url.startswith("/clips/"):
            return local_url
        file = CLIPS_DIR / local_url.removeprefix("/clips/")
        if not file.exists():
            return local_url
        path = f"{SITE}/{file.name}"
        ctype = "video/mp4" if file.suffix == ".mp4" else "image/jpeg"
        r = await self.client.post(
            f"{self.url}/storage/v1/object/{BUCKET}/{path}",
            headers={**self.headers, "content-type": ctype, "x-upsert": "true"},
            content=file.read_bytes(),
        )
        self._check(r)
        return self.public_url(path)

    async def write(self, event: SaesrEvent) -> None:
        clip_url = await self.upload(event.clip_url)
        frame_url = await self.upload(event.frame_url)
        row = {
            "id": event.id,
            "site": SITE,
            "camera": event.camera,
            "type": event.type,
            "severity": event.severity,
            "ts": event.timestamp,
            "zone": event.zone,
            "clip_url": clip_url,
            "frame_url": frame_url,
            "confidence": event.confidence,
            "bbox": list(event.bbox) if event.bbox else None,
            "detail": event.detail,
        }
        r = await self.client.post(
            f"{self.url}/rest/v1/incidents",
            headers={**self.headers, "content-type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=row,
        )
        self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code >= 500:
            raise httpx.HTTPStatusError(f"{r.status_code} {r.text[:200]}", request=r.request, response=r)
        if r.status_code >= 400:
            raise Rejected(f"{r.status_code} {r.text[:200]}")

    async def drain(self) -> None:
        """Deliver buffered events in order; retry the head with backoff."""
        attempt = 0
        while self.buffer:
            event = self.buffer[0]
            try:
                await self.write(event)
                self.buffer.popleft()
                self.sent += 1
                attempt = 0
            except Rejected as err:
                attempt += 1
                if attempt >= GIVE_UP_AFTER:
                    log.error("dropping %s after %d rejections: %s", event.id, attempt, err)
                    self.buffer.popleft()
                    self.failed += 1
                    attempt = 0
                    continue
                await asyncio.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
            except (httpx.HTTPError, OSError) as err:
                log.warning("supabase unreachable (%s); %d buffered", err, len(self.buffer))
                await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                attempt += 1

    async def run(self) -> None:
        q = bus.open_subscription()
        try:
            while True:
                self.buffer.append(await q.get())
                while not q.empty():
                    self.buffer.append(q.get_nowait())
                await self.drain()
        finally:
            bus.close_subscription(q)
            await self.client.aclose()


def start() -> Optional[asyncio.Task]:
    if not enabled():
        return None
    sink = SupabaseSink(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    log.info("supabase sink on (site=%s)", SITE)
    return asyncio.create_task(sink.run())
