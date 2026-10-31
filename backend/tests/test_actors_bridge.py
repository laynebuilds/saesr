"""Backend bridge (app/actors.py) against an in-process fake actors server.

The fake runs the REAL camera state machine from durable/src/actors.py (pure functions), so
these tests exercise the same transitions the actors server runs, without a server.
Run: cd backend && uv run --extra actors --group dev pytest -q tests/test_actors_*.py
"""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("durable_actors")

from app import actors, bus  # noqa: E402
from app.schema import Event  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "durable_actor_logic", Path(__file__).resolve().parents[2] / "durable" / "src" / "actors.py"
)
logic = importlib.util.module_from_spec(_spec)
sys.modules["durable_actor_logic"] = logic
_spec.loader.exec_module(logic)


class Down(Exception):
    """Connection-level failure (no .code): transient."""


class Rejected(Exception):
    code = "invalid_argument"


class FakeActors:
    """Camera/Site actors over the real pure state machine. `down` makes every call fail;
    restart() simulates an actors-server restart (state kept, new boot id)."""

    def __init__(self):
        self.cores: dict[str, logic.CameraCore] = {}
        self.boot = "boot-1"
        self.down = False
        self.fail_next: list[Exception] = []
        self.calls: list[tuple[str, str, str]] = []
        self.hang = False
        self.closed = False

    def restart(self):
        self.boot = f"boot-{int(self.boot.split('-')[1]) + 1}"

    def core(self, cam):
        return self.cores.setdefault(cam, logic.CameraCore(camera_id=cam))

    async def invoke(self, actor, actor_id, method, args):
        self.calls.append((actor, actor_id, method))
        if self.hang:
            await asyncio.sleep(3600)
        if self.down:
            raise Down("actors server down")
        if self.fail_next:
            raise self.fail_next.pop(0)
        now = int(time.time() * 1000)
        if actor == "Site":
            return logic.rollup([logic.to_state(c, now) for c in self.cores.values()]).model_dump(mode="json")
        core = self.core(actor_id)
        if method == "message":
            changed = logic.handle(core, args[0], now, self.boot)
            return logic.MessageResult(
                changed=changed, version=core.version, state=logic.to_state(core, now), outbox=list(core.outbox)
            ).model_dump(mode="json")
        logic.advance(core, now, self.boot)
        if method == "state":
            return logic.to_state(core, now).model_dump(mode="json")
        if method == "pending_relay":
            return [o.model_dump(mode="json") for o in core.outbox]
        if method == "relay_delivered":
            return logic.mark_delivered(core, args[0])
        raise Rejected(method)

    async def prepare_websocket(self, actor, actor_id, metadata):
        if self.down:
            raise Down("down")
        return {"websocketUrl": f"ws://fake/{actor}/{actor_id}", "homeRegion": "local", "connectByMs": 1}

    async def aclose(self):
        self.closed = True


class FakeRelay:
    def __init__(self, status: int = 200):
        self.status = status
        self.posts: list[tuple[str, dict, str]] = []

    async def __call__(self, url, body, key):
        self.posts.append((url, body, key))
        if isinstance(self.status, Exception):
            raise self.status
        return self.status


def ev(eid: str, cam: str = "cam-01", type_: str = "unsafe_lift", zone: str = "zone-pack-line") -> Event:
    return Event(event_id=eid, camera_id=cam, type=type_, confidence=0.9, zone_id=zone, keyframes=["a", "b", "c"])


def make_bridge(fake=None, relay=None, relay_url="http://relay.test", **kw) -> actors.Bridge:
    kw.setdefault("backoff", (0.0,))
    kw.setdefault("heartbeat_s", 0.01)
    kw.setdefault("dispatch_s", 0.01)
    return actors.Bridge(fake or FakeActors(), cameras=["cam-01", "cam-02"], relay_url=relay_url,
                         post=relay or FakeRelay(), watch=False, **kw)


async def until(cond, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


async def drain(bridge: actors.Bridge, cond, timeout: float = 2.0):
    task = asyncio.create_task(bridge.send_loop())
    try:
        await until(cond, timeout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    bus._subscribers.clear()
    actors._state_subs.clear()
    actors._last_version.clear()
    monkeypatch.setattr(actors, "_bridge", None)
    monkeypatch.setattr(actors, "_task", None)
    yield
    bus._subscribers.clear()
    actors._state_subs.clear()


def install(monkeypatch, bridge):
    monkeypatch.setattr(actors, "_bridge", bridge)
    return bridge


# Detection forwarding (acceptance 1) ------------------------------------------------


def test_bus_events_become_one_incident_and_duplicates_are_noops():
    async def scenario():
        fake = FakeActors()
        bridge = make_bridge(fake)
        pump = asyncio.create_task(bridge.pump())
        await asyncio.sleep(0)
        await bus.publish(ev("e1"))
        await bus.publish(ev("e1"))  # same event_id twice
        await until(lambda: len(bridge.buffer) == 2)
        await drain(bridge, lambda: not bridge.buffer)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        return fake, bridge

    fake, bridge = asyncio.run(scenario())
    core = fake.cores["cam-01"]
    assert len(core.incidents) == 1 and core.counters == {"unsafe_lift": 1}
    assert bridge.stats["sent"] == 2 and not bus._subscribers


def test_retries_until_server_is_back_and_keeps_order():
    fake = FakeActors()
    fake.fail_next = [Down("x"), Down("y")]
    bridge = make_bridge(fake)
    bridge.enqueue(ev("e1"))
    bridge.enqueue(ev("e2", zone="zone-machine-a"))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert [i.event_ids for i in fake.cores["cam-01"].incidents] == [["e1"], ["e2"]]
    assert bridge.stats["retries"] == 2 and bridge.connected


def test_buffer_bounded_drop_oldest():
    bridge = make_bridge(buffer_max=3)
    for i in range(5):
        bridge.enqueue(ev(f"e{i}"))
    assert [e.event_id for e in bridge.buffer] == ["e2", "e3", "e4"] and bridge.stats["dropped_overflow"] == 2


def test_rejected_event_dropped_after_max_failures():
    fake = FakeActors()
    fake.fail_next = [Rejected("bad")] * actors.MAX_PERMANENT_FAILURES
    bridge = make_bridge(fake)
    bridge.enqueue(ev("e1"))
    bridge.enqueue(ev("e2", zone="zone-machine-a"))
    asyncio.run(drain(bridge, lambda: not bridge.buffer))
    assert bridge.stats["dropped_rejected"] == 1
    assert [i.event_ids for i in fake.cores["cam-01"].incidents] == [["e2"]]


def test_heartbeats_every_camera_and_survive_outage():
    async def scenario():
        fake = FakeActors()
        bridge = make_bridge(fake)
        task = asyncio.create_task(bridge.heartbeat_loop())
        await until(lambda: bridge.stats["heartbeats"] >= 4)
        fake.down = True
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return fake, bridge, alive

    fake, bridge, alive = asyncio.run(scenario())
    assert alive and not bridge.connected
    assert fake.cores["cam-01"].last_heartbeat and fake.cores["cam-02"].last_heartbeat


# Relay dispatcher -------------------------------------------------------------------


def test_dispatch_posts_relay_start_with_idempotency_key_then_marks_delivered():
    fake, relay = FakeActors(), FakeRelay()
    bridge = make_bridge(fake, relay)

    async def scenario():
        await bridge.message("cam-01", {"kind": "detection", "event": ev("e1").model_dump(mode="json")})
        return await bridge.dispatch_once()

    assert asyncio.run(scenario()) == 1
    url, body, key = relay.posts[0]
    iid = fake.cores["cam-01"].incidents[0].incident_id
    assert url == "http://relay.test/relay/start" and key == f"{iid}:triage"
    assert body["step"] == "triage" and body["incident_id"] == iid and body["event"]["event_id"] == "e1"
    assert body["keyframes"] == ["a", "b", "c"] and body["action"] is None
    assert fake.cores["cam-01"].outbox == [] and bridge.stats["relay_pending"] == 0


def test_no_relay_url_leaves_items_pending_and_warns_once(caplog):
    fake = FakeActors()
    bridge = make_bridge(fake, relay_url=None)

    async def scenario():
        await bridge.message("cam-01", {"kind": "detection", "event": ev("e1").model_dump(mode="json")})
        await bridge.dispatch_once()
        await bridge.dispatch_once()

    with caplog.at_level("WARNING"):
        asyncio.run(scenario())
    assert len(fake.cores["cam-01"].outbox) == 1 and bridge.stats["relay_pending"] == 1
    assert sum("RELAY_URL not set" in r.message for r in caplog.records) == 1


def test_relay_error_keeps_item_pending():
    fake = FakeActors()
    for status in (500, Down("relay down")):
        bridge = make_bridge(fake, FakeRelay(status))

        async def scenario():
            await bridge.message("cam-01", {"kind": "detection", "event": ev("e1").model_dump(mode="json")})
            return await bridge.dispatch_once()

        assert asyncio.run(scenario()) == 0
        assert len(fake.cores["cam-01"].outbox) == 1 and bridge.stats["relay_failed"] == 1


# Acceptance 3: crash mid-relay, restart, resume, no double execution -------------------------


def test_backend_crash_mid_relay_resumes_and_never_double_counts(monkeypatch):
    fake, relay = FakeActors(), FakeRelay()
    send = lambda m: actors.send("cam-01", m)  # noqa: E731

    async def scenario():
        # Backend #1: incident opens; the relay accepts triage but the backend dies before
        # telling the actor (relay_delivered never arrives).
        b1 = install(monkeypatch, make_bridge(fake, relay))
        r = await send({"kind": "detection", "event": ev("e1").model_dump(mode="json")})
        assert r["ok"] and r["changed"]
        iid = r["state"]["incidents"][0]["incident_id"]

        async def die_on_delivered(actor, actor_id, method, args, _orig=fake.invoke):
            if method == "relay_delivered":
                raise asyncio.CancelledError  # the process is killed here
            return await _orig(actor, actor_id, method, args)

        monkeypatch.setattr(fake, "invoke", die_on_delivered)
        with pytest.raises(asyncio.CancelledError):
            await b1.dispatch_once()
        assert [p[2] for p in relay.posts] == [f"{iid}:triage"]
        monkeypatch.undo()  # restore FakeActors.invoke (also resets module globals)
        actors._state_subs.clear()

        # The actors server restarts too, for good measure.
        fake.restart()

        # Backend #2 starts: the still-pending triage call is resent with the same key.
        b2 = install(monkeypatch, make_bridge(fake, relay))
        assert await b2.dispatch_once() == 1
        assert [p[2] for p in relay.posts] == [f"{iid}:triage", f"{iid}:triage"]
        assert fake.cores["cam-01"].recovered_from_version is not None

        # The relay carries on: triage -> coach -> fixer with one tier-0 action.
        for m in (
            {"kind": "relay_update", "incident_id": iid, "step": "triage", "status": "confirmed", "output": {"severity": 4}},
            {"kind": "relay_update", "incident_id": iid, "step": "coach", "status": "done"},
            {"kind": "action_update", "incident_id": iid,
             "action": {"action_id": "a1", "incident_id": iid, "kind": "assign_coaching", "status": "done"}},
            {"kind": "relay_update", "incident_id": iid, "step": "fixer", "status": "done"},
        ):
            assert (await send(m))["ok"]
            await b2.dispatch_once()
        # Re-sent action update after the crash: no-op.
        again = await send({"kind": "action_update", "incident_id": iid,
                            "action": {"action_id": "a1", "incident_id": iid, "kind": "assign_coaching", "status": "done"}})
        return iid, again

    iid, again = asyncio.run(scenario())
    assert again["ok"] and again["changed"] is False and again["state"]["autonomous_fixes"] == 1
    inc = fake.cores["cam-01"].incidents[0]
    assert inc.status == "actioned" and fake.cores["cam-01"].autonomous_fixes == 1
    keys = [p[2] for p in relay.posts]
    assert keys == [f"{iid}:triage", f"{iid}:triage", f"{iid}:coach", f"{iid}:fixer", f"{iid}:verifier"]


def test_approve_flow_executes_gated_action_once(monkeypatch):
    fake, relay = FakeActors(), FakeRelay()
    install(monkeypatch, make_bridge(fake, relay))

    async def scenario():
        r = await actors.send("cam-01", {"kind": "detection", "event": ev("e1").model_dump(mode="json")})
        iid = r["state"]["incidents"][0]["incident_id"]
        await actors.send("cam-01", {"kind": "relay_update", "incident_id": iid, "step": "triage", "status": "confirmed"})
        await actors.send("cam-01", {"kind": "action_update", "incident_id": iid, "action": {
            "action_id": "s1", "incident_id": iid, "kind": "slow_machine", "status": "awaiting_approval"}})
        await actors.current().dispatch_once()
        a = await actors.send("cam-01", {"kind": "approve", "incident_id": iid, "action_id": "s1", "user": "jas"})
        b = await actors.send("cam-01", {"kind": "approve", "incident_id": iid, "action_id": "s1", "user": "jas"})
        await actors.current().dispatch_once()
        return iid, a, b

    iid, a, b = asyncio.run(scenario())
    assert a["changed"] and not b["changed"]
    exec_posts = [p for p in relay.posts if p[2] == f"{iid}:exec:s1"]
    assert len(exec_posts) == 1 and exec_posts[0][1]["action"]["status"] == "approved"


# Module interface -----------------------------------------------------------------------


def test_send_validates_and_handles_disabled_unknown_and_timeouts(monkeypatch):
    assert asyncio.run(actors.send("cam-01", {"kind": "heartbeat"})) == {"ok": False, "error": "actors disabled"}
    bad = asyncio.run(actors.send("cam-01", {"kind": "nope"}))
    assert bad["ok"] is False and "invalid message" in bad["error"]
    fake = FakeActors()
    install(monkeypatch, make_bridge(fake))
    assert "unknown camera" in asyncio.run(actors.send("cam-99", {"kind": "heartbeat"}))["error"]
    ok = asyncio.run(actors.send("cam-02", {"kind": "heartbeat", "fps": 9}))
    assert ok["ok"] and ok["changed"] and ok["state"]["camera_id"] == "cam-02"
    fake.down = True
    down = asyncio.run(actors.send("cam-01", {"kind": "heartbeat"}))
    assert down["ok"] is False and "down" in down["error"]
    fake.down, fake.hang = False, True
    monkeypatch.setattr(actors, "CALL_TIMEOUT_S", 0.05)
    t = time.monotonic()
    hung = asyncio.run(actors.send("cam-01", {"kind": "heartbeat"}))
    assert hung == {"ok": False, "error": "actors timeout"} and time.monotonic() - t < 2


def test_state_reads_grant_and_status(monkeypatch):
    assert asyncio.run(actors.camera_state("cam-01")) is None and asyncio.run(actors.site_state()) is None
    assert "error" in asyncio.run(actors.socket_grant("site", "site-demo"))
    assert actors.status()["running"] is False
    fake = FakeActors()
    install(monkeypatch, make_bridge(fake))
    asyncio.run(actors.send("cam-01", {"kind": "detection", "event": ev("e1").model_dump(mode="json")}))
    cam = asyncio.run(actors.camera_state("cam-01"))
    site = asyncio.run(actors.site_state())
    assert cam["camera_id"] == "cam-01" and len(cam["incidents"]) == 1
    assert site["site_id"] == "site-demo" and site["active_incidents"] == 1
    grant = asyncio.run(actors.socket_grant("camera", "cam-01"))
    assert grant["websocketUrl"] == "ws://fake/Camera/cam-01"
    st = actors.status()
    assert st["enabled"] is True and st["connected"] is True and st["cameras"] == ["cam-01", "cam-02"]
    fake.down = True
    assert asyncio.run(actors.site_state()) is None


def test_state_subscription_bounded_drop_oldest_and_deduped(monkeypatch):
    q = actors.open_state_subscription()
    for v in range(actors.STATE_QUEUE_MAX + 10):
        actors.publish_state({"camera_id": "cam-01", "version": v})
    actors.publish_state({"camera_id": "cam-01", "version": 3})  # stale: ignored
    assert q.qsize() == actors.STATE_QUEUE_MAX and q.get_nowait()["version"] == 10
    actors.close_state_subscription(q)
    actors.publish_state({"camera_id": "cam-01", "version": 10_000})
    assert q.qsize() == actors.STATE_QUEUE_MAX - 1

    q2 = actors.open_state_subscription()
    install(monkeypatch, make_bridge(FakeActors()))
    asyncio.run(actors.send("cam-02", {"kind": "heartbeat"}))
    asyncio.run(actors.send("cam-02", {"kind": "detection", "event": ev("z", cam="cam-02").model_dump(mode="json")}))
    asyncio.run(actors.send("cam-02", {"kind": "detection", "event": ev("z", cam="cam-02").model_dump(mode="json")}))
    states = [q2.get_nowait() for _ in range(q2.qsize())]
    assert [s["camera_id"] for s in states] == ["cam-02", "cam-02"] and states[1]["version"] > states[0]["version"]


def test_disabled_without_url(monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_URL", None)
    assert actors.enabled() is False and actors.start(["cam-01"]) is None


def test_bad_sdk_never_crashes_startup(monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_URL", "http://127.0.0.1:1")

    def boom(url):
        raise ImportError("no durable_actors")

    monkeypatch.setattr(actors, "make_client", boom)
    assert actors.start(["cam-01"]) is None


def test_start_and_stop(monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_URL", "http://fake")
    monkeypatch.setattr(actors, "make_client", lambda url: FakeActors())

    async def scenario():
        task = actors.start(["cam-01"])
        assert task is not None and actors.current() is not None
        await asyncio.sleep(0.05)
        actors.stop()
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = asyncio.run(scenario())
    assert task.done() and actors.current() is None
