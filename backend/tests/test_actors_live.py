"""Bridge against a REAL actors server (skipped when none is reachable).

    cd durable && uv run durable-actors dev                                     # terminal 1
    cd backend && uv run --extra actors --group dev pytest -q tests/test_actors_live.py

Uses fresh camera ids per test, so it never touches cam-01 / cam-02 demo state.
"""
import asyncio
import os
import uuid

import httpx
import pytest

pytest.importorskip("durable_actors")

from app import actors  # noqa: E402
from app.schema import Event  # noqa: E402

URL = os.environ.get("ACTORS_URL") or os.environ.get("DURABLE_ACTORS_CONTROL_PLANE_URL") or "http://127.0.0.1:7100"

try:
    httpx.get(URL, timeout=1)
except Exception:
    pytest.skip(f"no actors server at {URL}", allow_module_level=True)


class Relay:
    def __init__(self):
        self.posts: list[tuple[str, dict, str]] = []

    async def __call__(self, url, body, key):
        self.posts.append((url, body, key))
        return 202


async def until(cond, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


def test_end_to_end_against_real_server(monkeypatch):
    cam = f"cam-live-{uuid.uuid4().hex[:6]}"
    relay = Relay()

    async def scenario():
        bridge = actors.Bridge(actors.make_client(URL), cameras=[cam], relay_url="http://relay.test", post=relay,
                               heartbeat_s=0.5, dispatch_s=0.2, backoff=(0.2,))
        monkeypatch.setattr(actors, "_bridge", bridge)
        q = actors.open_state_subscription()
        task = asyncio.create_task(bridge.run())
        try:
            await until(lambda: cam in bridge.watchers_connected)
            # Acceptance 1 through the real path: bus -> buffer -> actor, twice the same id.
            ev = Event(event_id=f"e-{cam}", camera_id=cam, type="unsafe_lift", confidence=0.9, zone_id="zone-pack-line")
            bridge.enqueue(ev)
            bridge.enqueue(ev)
            await until(lambda: bridge.stats["sent"] == 2)
            state = await actors.camera_state(cam)
            assert len(state["incidents"]) == 1
            iid = state["incidents"][0]["incident_id"]
            # The dispatcher posts triage and marks it delivered.
            await until(lambda: any(p[2] == f"{iid}:triage" for p in relay.posts))
            await until(lambda: bridge.stats["relay_pending"] == 0)
            # Relay answers through send(): coach is queued and dispatched.
            r = await actors.send(cam, {"kind": "relay_update", "incident_id": iid, "step": "triage",
                                        "status": "confirmed", "output": {"severity": 3}})
            assert r["ok"] and r["state"]["incidents"][0]["status"] == "confirmed"
            await until(lambda: any(p[2] == f"{iid}:coach" for p in relay.posts))
            # Socket watcher + send results fed the state subscription.
            seen = []
            while not q.empty():
                seen.append(q.get_nowait())
            assert seen and all(s["camera_id"] == cam for s in seen)
            versions = [s["version"] for s in seen]
            assert versions == sorted(versions) and len(set(versions)) == len(versions)
            grant = await actors.socket_grant("site", "site-demo")
            assert grant["websocketUrl"].startswith("ws")
            site = await actors.site_state()
            assert any(c["camera_id"] == cam for c in site["cameras"])
            assert actors.status()["connected"] is True
        finally:
            actors.close_state_subscription(q)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
