"""Bridge from the bus to the durable actors, tested against a fake actors client."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import actors, bus, main
from app.schema import SaesrEvent


def ev(i: int, camera: str = "cam-1") -> SaesrEvent:
    return SaesrEvent(id=f"evt_{i}", camera=camera, type="no_vest", severity="low", zone="dock")


class Down(Exception):
    """A connection-level failure (no .code): always transient."""


class Rejected(Exception):
    """An actor-level failure with a non-transient code, like ActorInvocationError."""

    code = "invalid_argument"


class FakeClient:
    """Mimics the Site actor's idempotency: records each event id once."""

    def __init__(self, fail_first: int = 0, error: type = Down):
        self.fail_left = fail_first
        self.error = error
        self.calls: list[str] = []  # every attempted event id, in order
        self.recorded: dict[str, dict] = {}
        self.heartbeats: list[tuple[str, float]] = []
        self.closed = False

    async def record(self, camera, event, site):
        self.calls.append(event["id"])
        if self.fail_left:
            self.fail_left -= 1
            raise self.error("actors server down")
        self.recorded.setdefault(event["id"], event)
        return {"event_id": event["id"], "duplicate": False}

    async def heartbeat(self, camera, fps, site):
        if self.fail_left:
            raise Down("down")
        self.heartbeats.append((camera, fps))

    async def snapshot(self, site):
        if self.fail_left:
            raise Down("down")
        return {"site": site, "total": len(self.recorded)}

    async def aclose(self):
        self.closed = True


def make_bridge(client, **kw) -> actors.Bridge:
    kw.setdefault("backoff", (0.0,))
    kw.setdefault("heartbeat_s", 0.01)
    return actors.Bridge(client, site="test-site", **kw)


async def drain(bridge: actors.Bridge, until, timeout: float = 2.0) -> None:
    task = asyncio.create_task(bridge.send_loop())
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while not until():
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.005)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_delivers_in_order():
    client = FakeClient()
    bridge = make_bridge(client)
    for i in range(5):
        bridge.enqueue(ev(i))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert client.calls == [f"evt_{i}" for i in range(5)]
    assert bridge.stats["sent"] == 5 and bridge.connected


def test_retries_same_event_until_server_is_back():
    client = FakeClient(fail_first=3)
    bridge = make_bridge(client)
    bridge.enqueue(ev(1))
    bridge.enqueue(ev(2))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    # The head event is retried (never skipped), then the rest follow in order.
    assert client.calls == ["evt_1"] * 4 + ["evt_2"]
    assert list(client.recorded) == ["evt_1", "evt_2"]
    assert bridge.stats["retries"] == 3 and bridge.stats["sent"] == 2


def test_buffer_is_bounded_and_drops_oldest():
    bridge = make_bridge(FakeClient(), buffer_max=3)
    for i in range(5):
        bridge.enqueue(ev(i))
    assert [e.id for e in bridge.buffer] == ["evt_2", "evt_3", "evt_4"]
    assert bridge.stats["dropped_overflow"] == 2


def test_rejected_event_is_dropped_not_blocking_the_queue():
    client = FakeClient(fail_first=actors.MAX_PERMANENT_FAILURES, error=Rejected)
    bridge = make_bridge(client)
    bridge.enqueue(ev(1))
    bridge.enqueue(ev(2))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert bridge.stats["dropped_rejected"] == 1
    assert list(client.recorded) == ["evt_2"]


def test_transient_codes_retry_forever():
    class Unavailable(Exception):
        code = "outcome_unknown"

    client = FakeClient(fail_first=actors.MAX_PERMANENT_FAILURES * 2, error=Unavailable)
    bridge = make_bridge(client)
    bridge.enqueue(ev(1))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert list(client.recorded) == ["evt_1"] and bridge.stats["dropped_rejected"] == 0


def test_duplicate_publish_is_harmless():
    # outcome_unknown retries and a re-published event both resend the same id; the
    # Site actor dedupes by id, which the fake mirrors.
    client = FakeClient()
    bridge = make_bridge(client)
    bridge.enqueue(ev(7))
    bridge.enqueue(ev(7))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert client.calls == ["evt_7", "evt_7"] and list(client.recorded) == ["evt_7"]


def test_pump_reads_every_bus_event_and_releases_subscription():
    async def scenario():
        bridge = make_bridge(FakeClient())
        task = asyncio.create_task(bridge.pump())
        await asyncio.sleep(0)
        assert len(bus._subscribers) == 1
        for i in range(3):
            await bus.publish(ev(i, camera=f"cam-{i}"))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return bridge

    bus._subscribers.clear()
    bridge = asyncio.run(scenario())
    assert [e.id for e in bridge.buffer] == ["evt_0", "evt_1", "evt_2"]
    assert bridge.cameras == {"cam-0", "cam-1", "cam-2"}
    assert not bus._subscribers


def test_heartbeat_reports_fps_for_known_cameras():
    async def scenario():
        client = FakeClient()
        bridge = make_bridge(client, cameras=["cam-a", "cam-b"])
        task = asyncio.create_task(bridge.heartbeat_loop())
        for _ in range(5):
            bus.set_frame(b"jpeg")
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client

    client = asyncio.run(scenario())
    assert {c for c, _ in client.heartbeats} == {"cam-a", "cam-b"}
    assert any(fps > 0 for _, fps in client.heartbeats)


def test_heartbeat_survives_server_down():
    async def scenario():
        bridge = make_bridge(FakeClient(fail_first=10**6), cameras=["cam-a"])
        task = asyncio.create_task(bridge.heartbeat_loop())
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return alive, bridge

    alive, bridge = asyncio.run(scenario())
    assert alive and not bridge.connected and "down" in bridge.last_error


def test_disabled_without_url(monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_URL", None)
    assert actors.start() is None


def test_bad_sdk_never_crashes_startup(monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_URL", "http://127.0.0.1:1")

    def boom(url):
        raise ImportError("no durable_actors")

    monkeypatch.setattr(actors, "SdkClient", boom)
    assert actors.start() is None


def test_site_endpoint_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(actors, "_bridge", None)
    with TestClient(main.app) as c:
        bus._recent.clear()
        asyncio.run(bus.publish(ev(1)))
        body = c.get("/site").json()
        assert body["source"] == "local" and body["total"] == 1 and body["by_camera"] == {"cam-1": 1}
        assert c.get("/actors/status").json() == {"enabled": False}


def test_site_endpoint_reads_through_actors(monkeypatch):
    with TestClient(main.app) as c:
        monkeypatch.setattr(actors, "_bridge", make_bridge(FakeClient()))
        assert c.get("/site").json() == {"source": "actors", "site": "test-site", "total": 0}
        # Actors down: same endpoint, local answer, no error.
        monkeypatch.setattr(actors, "_bridge", make_bridge(FakeClient(fail_first=1)))
        assert c.get("/site").json()["source"] == "local"


@pytest.fixture(autouse=True)
def clean():
    bus._recent.clear()
    bus._subscribers.clear()
    yield
    bus._subscribers.clear()
