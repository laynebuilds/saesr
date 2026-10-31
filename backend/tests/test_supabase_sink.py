import asyncio
import json

import httpx

from app import supabase_sink
from app.schema import SaesrEvent
from app.supabase_sink import SupabaseSink


def ev(i: int, clip: str | None = None) -> SaesrEvent:
    return SaesrEvent(id=f"evt_{i}", camera="cam-1", type="unsafe_lift", severity="high", zone="loading-dock", clip_url=clip)


def sink_with(handler) -> SupabaseSink:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseSink("https://x.supabase.co", "sb_secret_test", client)


def test_writes_row_with_upsert_and_secret_key():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(201)

    s = sink_with(handler)
    s.buffer.append(ev(1, clip="/samples/unsafe_lift.mp4"))
    asyncio.run(s.drain())
    assert s.sent == 1 and not s.buffer
    req = seen[-1]
    assert req.url.path == "/rest/v1/incidents"
    assert "merge-duplicates" in req.headers["prefer"] and req.headers["apikey"] == "sb_secret_test"
    row = json.loads(req.content)
    assert row["id"] == "evt_1" and row["clip_url"] == "/samples/unsafe_lift.mp4"


def test_uploads_local_clip_and_rewrites_url(tmp_path, monkeypatch):
    monkeypatch.setattr(supabase_sink, "CLIPS_DIR", tmp_path)
    (tmp_path / "evt_2.mp4").write_bytes(b"mp4")
    paths = []

    def handler(req: httpx.Request):
        paths.append(req.url.path)
        return httpx.Response(200)

    s = sink_with(handler)
    s.buffer.append(ev(2, clip="/clips/evt_2.mp4"))
    asyncio.run(s.drain())
    assert "/storage/v1/object/clips/demo-warehouse/evt_2.mp4" in paths
    assert s.sent == 1


def test_retries_server_errors_then_delivers_in_order(monkeypatch):
    monkeypatch.setattr(supabase_sink, "BACKOFF_S", (0,))
    calls = {"n": 0}
    order = []

    def handler(req: httpx.Request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503)
        order.append(json.loads(req.content)["id"])
        return httpx.Response(201)

    s = sink_with(handler)
    s.buffer.extend([ev(1), ev(2)])
    asyncio.run(s.drain())
    assert order == ["evt_1", "evt_2"] and s.sent == 2


def test_drops_event_after_repeated_rejection(monkeypatch):
    monkeypatch.setattr(supabase_sink, "BACKOFF_S", (0,))

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        return httpx.Response(400 if body["id"] == "evt_bad" else 201)

    s = sink_with(handler)
    s.buffer.extend([ev(0), SaesrEvent(id="evt_bad", camera="c", type="no_vest", severity="low", zone="z"), ev(3)])
    asyncio.run(s.drain())
    assert s.failed == 1 and s.sent == 2 and not s.buffer


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert supabase_sink.start() is None
