import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import bus, main
from app.schema import SaesrEvent


def ev(i: int, severity: str = "high", hours_ago: float = 0.0, type_: str = "unsafe_lift") -> SaesrEvent:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return SaesrEvent(id=f"evt_{i}", camera="cam-1", type=type_, severity=severity, timestamp=ts, zone="loading-dock")


@pytest.fixture(autouse=True)
def clean_bus():
    bus._recent.clear()
    bus._subscribers.clear()
    if bus.EVENTS_LOG.exists():
        bus.EVENTS_LOG.unlink()
    yield


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        bus._recent.clear()  # drop anything the mock produced at startup
        yield c


def test_health(client):
    assert client.get("/health").json() == {"ok": True, "mode": "mock"}


def test_recent_returns_contract_shape_newest_last(client):
    for i in range(3):
        asyncio.run(bus.publish(ev(i)))
    body = client.get("/events/recent?limit=2").json()
    assert [e["id"] for e in body] == ["evt_1", "evt_2"]
    assert set(body[0]) >= {"id", "camera", "type", "severity", "timestamp", "zone", "clip_url"}


def test_recent_hours_filter(client):
    asyncio.run(bus.publish(ev(1, hours_ago=200)))
    asyncio.run(bus.publish(ev(2, hours_ago=1)))
    ids = [e["id"] for e in client.get("/events/recent?hours=168").json()]
    assert ids == ["evt_2"]


def test_summary_score_formula(client):
    # Two high (5 each) in a 1 hour minimum window: 10 per hour * 2 = 20 points off.
    asyncio.run(bus.publish(ev(1, "high")))
    asyncio.run(bus.publish(ev(2, "high")))
    s = client.get("/summary").json()
    assert s["total"] == 2
    assert s["weighted_incidents_per_hour"] == 10
    assert s["compliance_score"] == 80
    assert s["by_type"] == {"unsafe_lift": 2}


def test_summary_score_floors_at_zero(client):
    for i in range(30):
        asyncio.run(bus.publish(ev(i, "high")))
    assert client.get("/summary").json()["compliance_score"] == 0


def test_summary_window_excludes_old_events(client):
    asyncio.run(bus.publish(ev(1, hours_ago=30)))
    assert client.get("/summary").json()["total"] == 0
    assert client.get("/summary?hours=48").json()["total"] == 1


def test_events_persist_and_replay():
    asyncio.run(bus.publish(ev(1)))
    asyncio.run(bus.publish(ev(2)))
    bus._recent.clear()
    with bus.EVENTS_LOG.open("a") as f:
        f.write("not json\n")  # a corrupt line must be skipped, not fatal
    assert bus.load_history() == 2
    assert [e.id for e in bus.recent()] == ["evt_1", "evt_2"]


def test_slow_subscriber_drops_oldest_not_memory():
    async def scenario():
        gen = bus.subscribe()
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        for i in range(bus.SUBSCRIBER_QUEUE_MAX + 50):
            await bus.publish(ev(i))
        got = await first
        q = next(iter(bus._subscribers))
        assert q.qsize() <= bus.SUBSCRIBER_QUEUE_MAX
        await gen.aclose()
        return got

    assert asyncio.run(scenario()).id.startswith("evt_")


def test_frame_endpoints(client):
    assert client.get("/frame.jpg").status_code in (200, 404)
    bus.set_frame(b"\xff\xd8fake\xff\xd9")
    r = client.get("/frame.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_supervisor_restarts_crashed_producer():
    runs = []

    async def flaky():
        runs.append(1)
        raise RuntimeError("camera unplugged")

    async def scenario():
        old = main.RESTART_BACKOFF_S
        main.RESTART_BACKOFF_S = (0,)
        task = asyncio.create_task(main._supervise(flaky))
        await asyncio.sleep(0.05)
        task.cancel()
        main.RESTART_BACKOFF_S = old

    asyncio.run(scenario())
    assert len(runs) >= 2
