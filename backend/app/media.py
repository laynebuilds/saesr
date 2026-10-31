"""Media upload for incident evidence (keyframes and clips).

upload(local_path, bucket) -> url. With SUPABASE_URL and SUPABASE_SECRET_KEY set, the file goes to
Supabase Storage at {bucket}/{site_id}/{filename} and the public URL comes back. Otherwise, or on any
failure, it logs and returns the local URL /clips/<filename> (the API serves backend/clips at /clips).
It never raises: a storage outage must not stop the pipeline.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

import httpx

from .schema import SITE_ID

log = logging.getLogger("saesr.media")

BUCKETS = ("keyframes", "clips")
TIMEOUT_S = 20.0


def local_url(local_path) -> str:
    return f"/clips/{Path(local_path).name}"


def _content_type(path: Path) -> str:
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return "image/jpeg"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def final_url(local_path, bucket: str, site_id: str = SITE_ID) -> str:
    """The URL upload() returns on success, known before the file exists (for clips still recording)."""
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY") or ""
    if not url or not key or bucket not in BUCKETS:
        return local_url(local_path)
    return f"{url}/storage/v1/object/public/{bucket}/{site_id}/{Path(local_path).name}"


def upload(local_path, bucket: str, site_id: str = SITE_ID, client: Optional[httpx.Client] = None) -> str:
    """Upload one file and return its URL. `client` is for tests (httpx.MockTransport)."""
    path = Path(local_path)
    fallback = local_url(path)
    try:
        url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        key = os.environ.get("SUPABASE_SECRET_KEY") or ""
        if not url or not key:
            return fallback
        if bucket not in BUCKETS:
            log.warning("unknown bucket %r, keeping %s local", bucket, path.name)
            return fallback
        data = path.read_bytes()
        obj = f"{bucket}/{site_id}/{path.name}"
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "content-type": _content_type(path),
            "x-upsert": "true",
        }
        own = client is None
        c = client or httpx.Client(timeout=TIMEOUT_S)
        try:
            resp = c.post(f"{url}/storage/v1/object/{obj}", content=data, headers=headers)
        finally:
            if own:
                c.close()
        if resp.status_code >= 300:
            log.warning("upload %s to %s failed: HTTP %s %s", path.name, bucket, resp.status_code, resp.text[:200])
            return fallback
        return final_url(path, bucket, site_id)
    except Exception as err:  # never raise into the pipeline
        log.warning("upload %s to %s failed: %s", path.name, bucket, err)
        return fallback
