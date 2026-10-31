import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import actors, bus, main, mock
from app.schema import Event


def ev(i, hours_ago: float = 0.0, type_: str = "unsafe_lift", camera: str = "cam-01", zone: str = "zone-pack-line") -> Event:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return Event(event_id=f"evt_{i}", camera_id=camera, type=type_, confidence=0.9, timestamp=ts, zone_id=zone)


@pytest.fixture(autouse=True)
def clean_bus():
    bus.reset()
    if bus.EVENTS_LOG.exists():
        bus.EVENTS_LOG.unlink()
    main._machine_calls.clear()
    yield
    bus.reset()


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        bus.reset()  # drop anything the mock produced at startup
        yield c


class FakeActors:
    """Stands in for app.actors (the agreed interface)."""

    def __init__(self, on: bool = True):
        self.on = on
        self.sent: list[tuple[str, dict]] = []
        self.grants: list[tuple[str, str]] = []
        self.send_result = {"ok": True, "version": 3}

    def install(self, monkeypatch):
        monkeypatch.setattr(actors, "enabled", lambda: self.on)
        monkeypatch.setattr(actors, "send", self.send)
        monkeypatch.setattr(actors, "camera_state", self.camera_state)
        monkeypatch.setattr(actors, "site_state", self.site_state)
        monkeypatch.setattr(actors, "socket_grant", self.socket_grant)
        monkeypatch.setattr(actors, "status", lambda: {"enabled": self.on, "fake": True})
        return self

    async def send(self, camera_id, msg):
        self.sent.append((camera_id, msg))
        return self.send_result

    async def camera_state(self, camera_id):
        return None if camera_id == "cam-gone" else {"camera_id": camera_id, "version": 7, "incidents": []}

    async def site_state(self):
        return {"site_id": "site-demo", "site_score": 92.5, "cameras": []}

    async def socket_grant(self, kind, actor_id):
        self.grants.append((kind, actor_id))
        return {"websocketUrl": f"ws://actors/{kind}/{actor_id}", "token": "t"}


@pytest.fixture
def fake_actors(monkeypatch):
    return FakeActors().install(monkeypatch)


# ---- basics


def test_health(client):
    assert client.get("/health").json() == {"ok": True, "mode": "mock"}


def test_cors_is_open(client):
    r = client.options("/events", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "http://localhost:3000")


# ---- POST /events


def test_post_event_publishes_and_dedupes(client):
    body = ev("a1").model_dump(mode="json")
    r = client.post("/events", json=body)
    assert r.json() == {"ok": True, "event_id": "evt_a1", "duplicate": False}
    r2 = client.post("/events", json=body)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert [e.event_id for e in bus.recent()] == ["evt_a1"]


def test_post_event_generates_id_when_missing(client):
    r = client.post("/events", json={"camera_id": "cam-02", "type": "missing_gloves", "confidence": 0.8, "zone_id": "zone-handwash"})
    assert r.status_code == 200 and len(r.json()["event_id"]) > 10


@pytest.mark.parametrize("bad", [
    {"camera_id": "cam-01", "type": "no_hardhat", "confidence": 0.9, "zone_id": "z"},  # v1 type
    {"camera_id": "cam-01", "type": "unsafe_lift", "confidence": 1.5, "zone_id": "z"},  # out of range
    {"type": "unsafe_lift", "confidence": 0.9, "zone_id": "z"},  # no camera
    {"camera_id": "cam-01", "type": "unsafe_lift", "confidence": 0.9},  # no zone
])
def test_post_event_rejects_bad_input(client, bad):
    assert client.post("/events", json=bad).status_code == 422
    assert bus.recent() == []


def test_post_event_reaches_sse_subscribers(client):
    async def scenario():
        q = bus.open_subscription()
        await main.post_event(ev("sse"))
        got = q.get_nowait()
        bus.close_subscription(q)
        return got

    assert asyncio.run(scenario()).event_id == "evt_sse"


# ---- events/recent, summary


def test_recent_returns_v2_shape_newest_last(client):
    for i in range(3):
        asyncio.run(bus.publish(ev(i)))
    body = client.get("/events/recent?limit=2").json()
    assert [e["event_id"] for e in body] == ["evt_1", "evt_2"]
    assert set(body[0]) >= {"event_id", "camera_id", "type", "timestamp", "zone_id", "keyframes", "clip_url", "metrics"}


def test_recent_filters(client):
    asyncio.run(bus.publish(ev(1, hours_ago=200)))
    asyncio.run(bus.publish(ev(2, hours_ago=1)))
    asyncio.run(bus.publish(ev(3, camera="cam-02", type_="missing_hairnet", zone="zone-handwash")))
    assert [e["event_id"] for e in client.get("/events/recent?hours=168").json()] == ["evt_2", "evt_3"]
    assert [e["event_id"] for e in client.get("/events/recent?camera_id=cam-02").json()] == ["evt_3"]


def test_summary_counts_violations_and_score(client):
    asyncio.run(bus.publish(ev(1)))
    asyncio.run(bus.publish(ev(2, type_="zone_breach", zone="zone-machine-a")))
    asyncio.run(bus.publish(ev(3, type_="compliant_observation")))
    s = client.get("/summary").json()
    assert s["total"] == 2 and s["compliant_observations"] == 1
    assert s["weighted_incidents_per_hour"] == 10 and s["compliance_score"] == 80
    assert s["by_type"] == {"unsafe_lift": 1, "zone_breach": 1}
    assert s["by_zone"] == {"zone-pack-line": 1, "zone-machine-a": 1}
    assert s["by_camera"] == {"cam-01": 2}
    assert s["last_event"]["type"] == "compliant_observation"


def test_summary_floors_at_zero_and_window(client):
    for i in range(30):
        asyncio.run(bus.publish(ev(i)))
    assert client.get("/summary").json()["compliance_score"] == 0
    bus.reset()
    asyncio.run(bus.publish(ev("old", hours_ago=30)))
    assert client.get("/summary").json()["total"] == 0
    assert client.get("/summary?hours=48").json()["total"] == 1


# ---- bus


def test_events_persist_and_replay_skipping_bad_and_v1_lines():
    asyncio.run(bus.publish(ev(1)))
    asyncio.run(bus.publish(ev(2)))
    bus.reset()
    with bus.EVENTS_LOG.open("a") as f:
        f.write("not json\n")
        f.write(json.dumps({"id": "evt_old", "camera": "cam-1", "type": "no_vest", "severity": "low"}) + "\n")
        f.write(ev(1).model_dump_json() + "\n")  # duplicate of an earlier line
    assert bus.load_history() == 2
    assert [e.event_id for e in bus.recent()] == ["evt_1", "evt_2"]


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

    assert asyncio.run(scenario()).event_id.startswith("evt_")


def test_per_camera_frames():
    assert bus.latest_frame() is None and bus.frame_seq() == 0
    bus.set_frame(b"one")  # default camera cam-01
    bus.set_frame(b"two", "cam-02")
    assert bus.latest_frame("cam-01") == b"one" and bus.latest_frame("cam-02") == b"two"
    assert bus.latest_frame() == b"two"  # most recent of any camera
    assert bus.frame_seq("cam-01") == 1 and bus.frame_seq("cam-02") == 1 and bus.frame_seq() == 2
    assert bus.latest_frame("cam-09") is None and bus.frame_seq("cam-09") == 0


# ---- frames


def test_frame_endpoints(client):
    assert client.get("/frame.jpg").status_code == 404
    bus.set_frame(b"\xff\xd8a\xff\xd9", "cam-01")
    bus.set_frame(b"\xff\xd8b\xff\xd9", "cam-02")
    r = client.get("/frame.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"\xff\xd8b\xff\xd9"
    assert client.get("/frame.jpg?camera_id=cam-01").content == b"\xff\xd8a\xff\xd9"
    cams = {c["camera_id"]: c["has_frame"] for c in client.get("/cameras").json()}
    assert cams == {"cam-01": True, "cam-02": True}


def test_mjpeg_sends_only_changed_frames_of_that_camera(monkeypatch):
    monkeypatch.setattr(main, "MJPEG_FPS", 1000)

    async def scenario():
        bus.set_frame(b"A1", "cam-01")
        ticks = {"n": 0}

        async def disconnected():
            ticks["n"] += 1
            if ticks["n"] == 3:
                bus.set_frame(b"B1", "cam-02")  # other camera: must not be sent
            if ticks["n"] == 5:
                bus.set_frame(b"A2", "cam-01")
            return ticks["n"] > 8

        return [c async for c in main.mjpeg("cam-01", disconnected)]

    chunks = asyncio.run(scenario())
    assert len(chunks) == 2
    assert chunks[0].startswith(b"--frame\r\nContent-Type: image/jpeg") and chunks[0].endswith(b"A1\r\n")
    assert chunks[1].endswith(b"A2\r\n")


def test_stream_route_rejects_bad_camera_id(client):
    assert client.get("/stream/bad%20id").status_code == 422


# ---- actors routes


def test_actor_routes_503_when_actors_off(client, monkeypatch):
    FakeActors(on=False).install(monkeypatch)
    msg = {"kind": "heartbeat", "fps": 9.5}
    assert client.post("/internal/cameras/cam-01/message", json=msg).status_code == 503
    assert client.get("/cameras/cam-01/state").status_code == 503
    assert client.get("/site/state").status_code == 503
    assert client.post("/api/cameras/cam-01/socket").status_code == 503
    assert client.post("/api/site/socket").status_code == 503


def test_internal_message_validates_and_forwards(client, fake_actors):
    msg = {"kind": "relay_update", "incident_id": "inc_1", "step": "triage", "status": "confirmed",
           "output": {"severity": 4}, "trace": {"agent": "triage", "started_at": "2026-10-31T12:00:00Z"}}
    r = client.post("/internal/cameras/cam-01/message", json=msg)
    assert r.status_code == 200 and r.json() == {"ok": True, "version": 3}
    camera_id, sent = fake_actors.sent[0]
    assert camera_id == "cam-01" and sent["kind"] == "relay_update" and sent["trace"]["agent"] == "triage"
    assert sent["output"] == {"severity": 4}


@pytest.mark.parametrize("bad", [
    {"kind": "nope"},
    {"kind": "relay_update", "incident_id": "inc_1", "step": "bogus", "status": "done"},
    {"kind": "approve", "incident_id": "inc_1"},  # missing action_id, user
    {"kind": "action_update", "incident_id": "i", "action": {"action_id": "a", "incident_id": "i", "kind": "launch_rocket"}},
])
def test_internal_message_rejects_bad_messages(client, fake_actors, bad):
    assert client.post("/internal/cameras/cam-01/message", json=bad).status_code == 422
    assert fake_actors.sent == []


def test_internal_message_all_kinds_accepted(client, fake_actors):
    e = ev("d1").model_dump(mode="json")
    msgs = [
        {"kind": "detection", "event": e},
        {"kind": "action_update", "incident_id": "i", "action": {"action_id": "a", "incident_id": "i", "kind": "slow_machine", "status": "awaiting_approval"}},
        {"kind": "approve", "incident_id": "i", "action_id": "a", "user": "jas"},
        {"kind": "close", "incident_id": "i"},
        {"kind": "heartbeat"},
    ]
    for m in msgs:
        assert client.post("/internal/cameras/cam-02/message", json=m).status_code == 200
    assert [m["kind"] for _, m in fake_actors.sent] == ["detection", "action_update", "approve", "close", "heartbeat"]


def test_internal_message_actor_failure_is_502(client, fake_actors):
    fake_actors.send_result = {"ok": False, "error": "unavailable"}
    r = client.post("/internal/cameras/cam-01/message", json={"kind": "heartbeat"})
    assert r.status_code == 502 and r.json()["error"] == "unavailable"


def test_internal_token(client, fake_actors, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    assert client.post("/internal/cameras/cam-01/message", json={"kind": "heartbeat"}).status_code == 401
    r = client.post("/internal/cameras/cam-01/message", json={"kind": "heartbeat"}, headers={"X-Internal-Token": "s3cret"})
    assert r.status_code == 200


def test_state_and_socket_routes(client, fake_actors):
    assert client.get("/cameras/cam-01/state").json()["version"] == 7
    assert client.get("/cameras/cam-gone/state").status_code == 503
    assert client.get("/site/state").json()["site_score"] == 92.5
    assert client.post("/api/cameras/cam-02/socket").json()["websocketUrl"] == "ws://actors/camera/cam-02"
    assert client.post("/api/site/socket").json()["websocketUrl"] == "ws://actors/site/site-demo"
    assert fake_actors.grants == [("camera", "cam-02"), ("site", "site-demo")]
    assert client.get("/actors/status").json() == {"enabled": True, "fake": True}


# ---- mock machine


def test_mock_machine_slow(client):
    r = client.post("/mock/machine/slow", json={"machine_id": "machine-a", "incident_id": "inc_1", "speed_pct": 50})
    body = r.json()
    assert body["ok"] is True and body["simulated"] is True and body["machine_id"] == "machine-a"
    assert client.get("/mock/machine/calls").json()[0]["request"]["speed_pct"] == 50
    assert client.post("/mock/machine/slow").json()["ok"] is True  # empty body is fine


# ---- admin


def test_admin_crash_api_exits_after_responding(client, monkeypatch):
    exits = []
    monkeypatch.setattr(main, "CRASH_DELAY_S", 0)
    monkeypatch.setattr(main.os, "_exit", lambda code: exits.append(code))
    r = client.post("/admin/crash")
    assert r.status_code == 200 and r.json()["target"] == "api"
    client.get("/health")  # let the event loop run the scheduled exit
    assert exits == [1]


def test_admin_crash_actors_kills_pid_tree(client, monkeypatch, tmp_path):
    # a parent that spawns a child, like `uv run durable-actors dev`
    proc = subprocess.Popen([sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)"])
    try:
        import time
        time.sleep(0.5)
        pid_file = tmp_path / "actors.pid"
        pid_file.write_text(f"{proc.pid}\n")
        monkeypatch.setattr(main, "ACTORS_PID_FILE", pid_file)
        monkeypatch.setattr(main.os, "_exit", lambda code: pytest.fail("api must not exit for target=actors"))
        r = client.post("/admin/crash?target=actors")
        assert r.status_code == 200
        killed = r.json()["actors_killed"]
        assert proc.pid in killed and len(killed) == 2
        assert proc.wait(timeout=5) != 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_admin_crash_actors_without_pid_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ACTORS_PID_FILE", tmp_path / "missing.pid")
    assert client.post("/admin/crash?target=actors").status_code == 409
    assert client.post("/admin/crash?target=nope").status_code == 422


def test_admin_token_guards(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "boom")
    monkeypatch.setattr(main.os, "_exit", lambda code: None)
    assert client.post("/admin/crash").status_code == 401
    assert client.post("/admin/replay", json={"camera_id": "cam-01", "source": "x.mp4"}).status_code == 401
    assert client.delete("/admin/replay").status_code == 401
    assert client.post("/admin/crash", headers={"X-Admin-Token": "boom"}).status_code == 200


def test_admin_replay_503_without_live_extra(client, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_cv(name, *a, **k):
        if name in ("cv2", "ultralytics"):
            raise ImportError(f"No module named '{name}'", name=name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_cv)
    r = client.post("/admin/replay", json={"camera_id": "cam-01", "source": "clip.mp4"})
    assert r.status_code == 503 and "live CV extra" in r.json()["detail"]


def test_admin_replay_start_replace_list_stop(client, monkeypatch):
    calls = []

    class FakePipeline:
        @staticmethod
        async def run(publish, set_frame, source, camera_id="cam-01", loop=False):
            calls.append((source, camera_id, loop))
            set_frame(b"jpeg", camera_id)
            await asyncio.sleep(3600)

    monkeypatch.setattr(main, "_load_pipeline", lambda: FakePipeline)
    r = client.post("/admin/replay", json={"camera_id": "cam-02", "source": "hygiene.mp4"})
    assert r.status_code == 200 and r.json()["replaced"] is False and r.json()["running"] is True
    r = client.post("/admin/replay", json={"camera_id": "cam-02", "source": "hygiene2.mp4"})
    assert r.json()["replaced"] is True
    client.get("/health")
    listed = client.get("/admin/replay").json()
    assert [(x["camera_id"], x["source"], x["loop"]) for x in listed] == [("cam-02", "hygiene2.mp4", True)]
    assert ("hygiene2.mp4", "cam-02", True) in calls
    assert bus.latest_frame("cam-02") == b"jpeg"
    assert client.delete("/admin/replay?camera_id=cam-02").json()["stopped"] == ["cam-02"]
    assert client.get("/admin/replay").json() == []


# ---- producer


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


def test_mock_events_are_v2_and_sample_media_exists():
    for t in ("unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves", "compliant_observation"):
        e = mock.make_event(t)
        assert e.type == t and len(e.keyframes) == 3 and e.clip_url
        stem = e.keyframes[0].rsplit("/", 1)[1]
        assert (main.SAMPLES_DIR / stem).exists() and (main.SAMPLES_DIR / e.clip_url.rsplit("/", 1)[1]).exists()
    assert mock.make_event("missing_hairnet").camera_id == "cam-02"
    assert mock.make_event("zone_breach").zone_id in ("zone-machine-a", "zone-pack-line")
    assert {mock.make_event().type for _ in range(200)} == {"unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves"}


def test_mock_producer_sets_per_camera_frames(monkeypatch):
    async def scenario():
        publish = None

        async def capture(pub, every):
            nonlocal publish
            publish = pub

        monkeypatch.setattr(mock, "run", capture)
        await main._producer()()
        await publish(mock.make_event("missing_gloves"))

    asyncio.run(scenario())
    assert bus.latest_frame("cam-02") is not None and bus.recent()[-1].camera_id == "cam-02"


# ---- scripts/mock_events.py

import importlib.util  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from pydantic import TypeAdapter  # noqa: E402

from app.schema import ACTION_TIER, ActorMessage  # noqa: E402

_spec = importlib.util.spec_from_file_location("mock_events", Path(__file__).resolve().parent.parent / "scripts" / "mock_events.py")
mock_events = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_events)
_msg = TypeAdapter(ActorMessage)


def test_mock_script_demo_events_are_valid_v2():
    events = mock_events.demo_events("http://h:8000")
    parsed = [Event.model_validate(e) for e in events]
    assert [e.type for e in parsed] == ["unsafe_lift", "zone_breach", "missing_hairnet", "missing_gloves",
                                        "unsafe_lift", "compliant_observation"]
    assert parsed[0].event_id == parsed[4].event_id  # the duplicate
    assert len({e.event_id for e in parsed}) == 5
    assert parsed[2].camera_id == "cam-02" and parsed[2].zone_id == "zone-handwash"
    assert parsed[1].zone_id == "zone-machine-a"
    assert all(len(e.keyframes) == 3 and e.keyframes[0].startswith("http://h:8000/samples/") for e in parsed)
    for e in parsed:  # placeholder media really exists
        assert (main.SAMPLES_DIR / e.keyframes[0].rsplit("/", 1)[1]).exists()
        assert (main.SAMPLES_DIR / e.clip_url.rsplit("/", 1)[1]).exists()


def test_mock_script_relay_messages_are_valid_actor_messages():
    event = mock_events.event_payload("unsafe_lift")
    all_msgs = []
    for step in mock_events.STEPS:
        msgs = mock_events.step_messages(step, "inc_9", event)
        for m in msgs:
            _msg.validate_python(m)
        all_msgs += msgs
    updates = [m for m in all_msgs if m["kind"] == "relay_update"]
    assert [m["step"] for m in updates] == list(mock_events.STEPS)
    assert updates[0]["status"] == "confirmed" and updates[0]["output"]["severity"] == 4
    assert all(m["trace"]["agent"] == m["step"] and m["trace"]["started_at"] for m in updates)
    actions = {m["action"]["kind"]: m["action"] for m in all_msgs if m["kind"] == "action_update"}
    assert actions["assign_coaching"]["status"] == "done" and ACTION_TIER["assign_coaching"] == 0
    assert actions["open_corrective_record"]["status"] == "done"
    assert actions["slow_machine"]["status"] == "awaiting_approval" and ACTION_TIER["slow_machine"] == 2
    hygiene = mock_events.step_messages("fixer", "inc_h", mock_events.event_payload("missing_hairnet"))
    assert "flag_batch_hold" in [m["action"]["kind"] for m in hygiene if m["kind"] == "action_update"]


def test_mock_script_run_once_against_api(client):
    api = mock_events.Api("", client=client)
    results = mock_events.run_once(api)
    assert len(results) == 6 and sum(r["duplicate"] for r in results) == 1
    assert len(bus.recent()) == 5


def test_mock_script_stand_in_relay_full_cycle(client, fake_actors):
    api = mock_events.Api("", client=client)
    followups = []
    relay = mock_events.StandInRelay(api, step_delay=0, after_cycle=lambda inc, ev_: followups.append((inc, ev_)))
    server = relay.serve(0)
    try:
        event = mock_events.event_payload("unsafe_lift")
        start = {"incident_id": "inc_1", "camera_id": "cam-01", "step": "triage", "event": event}
        port = server.server_address[1]
        r = httpx_post(f"http://127.0.0.1:{port}/relay/start", start)
        assert r.status_code == 200 and r.json()["accepted"] == "inc_1"
        assert httpx_post(f"http://127.0.0.1:{port}/relay/start", start).json()["already_running"] is True
        assert httpx_post(f"http://127.0.0.1:{port}/relay/start", {"bad": 1}).status_code == 422
        wait_for(lambda: followups)
        kinds = [(m["kind"], m.get("step") or m["action"]["kind"]) for _, m in fake_actors.sent]
        assert kinds[0] == ("relay_update", "triage") and kinds[-1] == ("relay_update", "reporter")
        assert ("action_update", "slow_machine") in kinds and all(c == "cam-01" for c, _ in fake_actors.sent)
        # the actor asks the relay to execute the approved gated action
        slow = next(m["action"] for _, m in fake_actors.sent if m["kind"] == "action_update" and m["action"]["kind"] == "slow_machine")
        fake_actors.sent.clear()
        r = httpx_post(f"http://127.0.0.1:{port}/relay/start", {**start, "step": "fixer", "action": {**slow, "status": "approved"}})
        assert r.json()["executing"] == slow["action_id"]
        wait_for(lambda: fake_actors.sent)
        assert fake_actors.sent[0][1]["action"]["status"] == "done"
        assert client.get("/mock/machine/calls").json()[-1]["request"]["machine_id"] == "machine-a"
    finally:
        server.shutdown()


def httpx_post(url, body):
    import httpx
    return httpx.post(url, json=body, timeout=5)


def wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while not pred():
        assert time.time() < deadline, "condition not met in time"
        time.sleep(0.02)
