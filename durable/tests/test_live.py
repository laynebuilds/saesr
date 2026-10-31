"""Acceptance tests against a REAL `durable-actors dev` server that this module starts, kills
with SIGKILL and restarts (fresh data dir, free port). Skipped if the server cannot start.

    cd durable && uv run pytest tests/test_live.py -v

Short windows (VERIFY_WINDOW_S=2, RECOVERING_S=2) keep it fast.
"""
import json
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest

from durable_actors import Client

ROOT = Path(__file__).resolve().parent.parent
VERIFY_S = 2.0
RECOVER_S = 2.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, data_dir: Path):
        self.port = free_port()
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        env = {**os.environ, "VERIFY_WINDOW_S": str(VERIFY_S), "RECOVERING_S": str(RECOVER_S)}
        log = open(self.data_dir / "server.log", "ab")
        self.proc = subprocess.Popen(
            ["uv", "run", "durable-actors", "dev", "--no-watch", "--port", str(self.port), "--data-dir", str(self.data_dir)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                httpx.get(self.url, timeout=1)
                # Up: wait until actor calls work reliably. After a SIGKILL the runtime refuses
                # calls ("previous owner lease is still active") until the old lease expires.
                c = Client(control_plane_url=self.url)
                for _ in range(3):
                    c.invoke("Site", "warmup", "state", [])
                    time.sleep(0.3)
                return
            except Exception:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.5)
        self.kill()
        pytest.skip("durable-actors dev did not start: " + (self.data_dir / "server.log").read_text()[-2000:])

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)  # crash, not a clean shutdown
            self.proc.wait(10)
        self.proc = None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = Server(tmp_path_factory.mktemp("actors"))
    s.start()
    yield s
    s.kill()


def client(server: Server) -> Client:
    return Client(control_plane_url=server.url)


def cam_id() -> str:
    return f"cam-test-{uuid.uuid4().hex[:6]}"


def det(cam: str, eid: str, type_: str = "unsafe_lift", zone: str = "zone-pack-line") -> dict:
    return {"kind": "detection", "event": {"event_id": eid, "camera_id": cam, "type": type_, "confidence": 0.9,
                                           "zone_id": zone, "keyframes": ["k1", "k2", "k3"]}}


def msg(c: Client, cam: str, m: dict) -> dict:
    return c.invoke("Camera", cam, "message", [m])


def relay(iid: str, step: str, status: str = "done", **output) -> dict:
    return {"kind": "relay_update", "incident_id": iid, "step": step, "status": status, "output": output}


def act(iid: str, aid: str, kind: str, status: str) -> dict:
    return {"kind": "action_update", "incident_id": iid,
            "action": {"action_id": aid, "incident_id": iid, "kind": kind, "status": status}}


def deliver(c: Client, cam: str) -> list[str]:
    ids = [o["id"] for o in c.invoke("Camera", cam, "pending_relay", [])]
    for oid in ids:
        assert c.invoke("Camera", cam, "relay_delivered", [oid]) is True
    return ids


def to_actioned(c: Client, cam: str) -> str:
    eid = f"e-{uuid.uuid4().hex[:6]}"
    r = msg(c, cam, det(cam, eid))
    iid = r["state"]["incidents"][-1]["incident_id"]
    msg(c, cam, relay(iid, "triage", "confirmed", severity=4))
    msg(c, cam, relay(iid, "coach"))
    msg(c, cam, act(iid, "a1", "assign_coaching", "pending"))
    msg(c, cam, relay(iid, "fixer"))
    r = msg(c, cam, act(iid, "a1", "assign_coaching", "done"))
    inc = r["state"]["incidents"][-1]
    assert inc["status"] == "actioned" and inc["verify_until"]
    return iid


def retry(fn, timeout: float = 60.0):
    deadline = time.time() + timeout
    while True:
        try:
            return fn()
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def wait_for(fn, timeout: float = 15.0, step: float = 0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(step)
    raise AssertionError("condition not met in time")


# Acceptance 1 -------------------------------------------------------------------


def test_one_incident_per_event_and_duplicate_is_noop(server):
    c, cam = client(server), cam_id()
    first = msg(c, cam, det(cam, "dup-1"))
    again = msg(c, cam, det(cam, "dup-1"))
    assert first["changed"] and not again["changed"] and again["version"] == first["version"]
    st = c.invoke("Camera", cam, "state", [])
    assert len(st["incidents"]) == 1 and st["incidents"][0]["status"] == "verifying"
    assert [o["start"]["step"] for o in first["outbox"]] == ["triage"]


# Acceptance 2 + 3: crash the actors server mid-relay ------------------------------------


def test_kill_server_mid_relay_state_and_outbox_survive(server):
    c, cam = client(server), cam_id()
    iid = to_actioned(c, cam)
    deliver(c, cam)  # triage, coach, fixer, verifier accepted by the relay...
    msg(c, cam, relay(iid, "verifier"))  # ...verifier done: reporter queued, NOT yet delivered = mid-relay
    msg(c, cam, {"kind": "heartbeat"})
    before = c.invoke("Camera", cam, "state", [])
    pending_before = [o["id"] for o in c.invoke("Camera", cam, "pending_relay", [])]
    assert pending_before == [f"{iid}:reporter"]
    site_before = c.invoke("Site", "site-demo", "state", [])
    assert any(x["camera_id"] == cam for x in site_before["cameras"])

    server.kill()
    with pytest.raises(Exception):
        Client(control_plane_url=server.url).invoke("Camera", cam, "state", [])
    server.start()
    c = client(server)

    # Each actor's old owner lease must expire before the new process may load it; until then
    # calls fail with "unavailable" (the backend bridge retries the same way).
    t0 = time.time()
    after = retry(lambda: c.invoke("Camera", cam, "state", []), timeout=90)
    print(f"camera reloaded {time.time() - t0:.1f}s after the server came back")
    assert after["recovered_from_version"] == before["version"]
    assert after["version"] == before["version"] + 1  # the recovery itself is one mutation
    assert after["status"] == "recovering"
    inc_b, inc_a = before["incidents"][0], after["incidents"][0]
    # Still actioned, or resolved by the (short) verification window that ran out during the restart.
    assert inc_a["status"] in ("actioned", "resolved") and inc_a["actions"] == inc_b["actions"]
    assert inc_a["event_ids"] == inc_b["event_ids"] and inc_a["relay"] == inc_b["relay"]
    assert after["autonomous_fixes"] == before["autonomous_fixes"] == 1
    # The pending relay step is still there, so the dispatcher re-sends it.
    assert [o["id"] for o in c.invoke("Camera", cam, "pending_relay", [])] == pending_before
    # Re-sending the done action does not execute or count twice.
    again = msg(c, cam, act(iid, "a1", "assign_coaching", "done"))
    assert again["changed"] is False and again["state"]["autonomous_fixes"] == 1
    # Already-delivered steps are never queued again.
    msg(c, cam, relay(iid, "triage", "confirmed", severity=4))
    assert [o["id"] for o in c.invoke("Camera", cam, "pending_relay", [])] == pending_before
    # recovering -> online on its own (alarm), once heartbeats resume.
    msg(c, cam, {"kind": "heartbeat"})
    wait_for(lambda: c.invoke("Camera", cam, "state", [])["status"] == "online", timeout=RECOVER_S + 10)


# Acceptance 4: verification window ---------------------------------------------------


def test_violation_after_fix_is_recurring(server):
    c, cam = client(server), cam_id()
    iid = to_actioned(c, cam)
    deliver(c, cam)
    r = msg(c, cam, det(cam, f"again-{uuid.uuid4().hex[:6]}"))
    inc = r["state"]["incidents"][-1]
    assert inc["incident_id"] == iid and inc["status"] == "recurring"
    assert [o["id"] for o in r["outbox"]] == [f"{iid}:fixer:r1"]


def test_compliant_observation_in_window_resolves(server):
    c, cam = client(server), cam_id()
    to_actioned(c, cam)
    r = msg(c, cam, det(cam, f"ok-{uuid.uuid4().hex[:6]}", "compliant_observation"))
    inc = r["state"]["incidents"][-1]
    assert inc["status"] == "resolved" and inc["low_confidence"] is False


def test_timeout_resolves_low_confidence_via_alarm(server):
    c, cam = client(server), cam_id()
    to_actioned(c, cam)

    # Only the Camera's alarm can push this to the Site: nobody calls the Camera meanwhile.
    def resolved_on_site():
        site = c.invoke("Site", "site-demo", "state", [])
        cs = [x for x in site["cameras"] if x["camera_id"] == cam]
        return cs and cs[0]["incidents"][-1]["status"] == "resolved" and cs[0]["incidents"][-1]["low_confidence"]

    wait_for(resolved_on_site, timeout=VERIFY_S + 10)


def test_offline_after_heartbeats_stop_is_not_instant(server):
    c, cam = client(server), cam_id()
    r = msg(c, cam, {"kind": "heartbeat", "fps": 10})
    assert r["state"]["status"] == "online" and r["state"]["last_heartbeat"]


# Broadcasts ----------------------------------------------------------------------------


def test_camera_and_site_sockets_get_state_on_change(server):
    c, cam = client(server), cam_id()
    msg(c, cam, {"kind": "heartbeat"})
    got_cam: list = []
    got_site: list = []
    ready = threading.Barrier(3)

    def listen(actor: str, actor_id: str, sink: list, n: int) -> None:
        grant = c.prepare_websocket(actor, actor_id, {"name": "pytest"})
        from websockets.sync.client import connect

        with connect(grant.websocket_url) as ws:
            sink.append(json.loads(ws.recv()))  # state on connect
            ready.wait(10)
            while len(sink) < n:
                data = json.loads(ws.recv(timeout=10))
                if actor == "Site" and not any(x["camera_id"] == cam and x["incidents"] for x in data["cameras"]):
                    continue  # other tests' cameras
                sink.append(data)

    t1 = threading.Thread(target=listen, args=("Camera", cam, got_cam, 2), daemon=True)
    t2 = threading.Thread(target=listen, args=("Site", "site-demo", got_site, 2), daemon=True)
    t1.start()
    t2.start()
    ready.wait(15)
    msg(c, cam, det(cam, f"sock-{uuid.uuid4().hex[:6]}"))
    t1.join(15)
    t2.join(15)
    assert got_cam[0]["camera_id"] == cam and got_cam[1]["incidents"][0]["status"] == "verifying"
    assert got_cam[1]["version"] > got_cam[0]["version"]
    assert {"site_score", "active_incidents", "autonomous_fixes", "cameras"} <= set(got_site[1])
