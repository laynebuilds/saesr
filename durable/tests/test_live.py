"""Integration tests against a running actors server (skipped when none is reachable).

    cd durable && uv run durable-actors dev      # terminal 1
    cd durable && uv run pytest                  # terminal 2

Each test uses a fresh Site id so runs never interfere.
"""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

URL = os.environ.get("DURABLE_ACTORS_CONTROL_PLANE_URL", "http://127.0.0.1:7100")

try:
    httpx.get(URL, timeout=1)
    from generated import actors
except Exception:  # server down or client not generated
    pytest.skip(f"no actors server at {URL} (or run durable-actors generate)", allow_module_level=True)


def event(camera: str, n: int, severity: str = "high", model=None):
    """Each actor's generated client has its own SaesrEvent class; default is Camera's."""
    return (model or actors.Camera.SaesrEvent)(
        id=f"{camera}-{n}-{uuid.uuid4().hex[:6]}",
        camera=camera,
        type="no_hardhat",
        severity=severity,
        timestamp=datetime.now(timezone.utc).isoformat(),
        zone=f"zone-{camera}",
    )


def site_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def test_concurrent_cameras_lose_no_updates():
    """10 cameras x 20 events fired from 10 threads at once. Serialized execution at the Site
    means every increment lands: totals and per-camera counters are exact."""
    site = site_id()
    cams = [f"{site}-cam{i}" for i in range(10)]

    def burst(cam: str) -> None:
        ref = actors.Camera.get(cam)
        for n in range(20):
            ref.record(event(cam, n, "low"), site)

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(burst, cams))
    snap = actors.Site.get(site).snapshot()
    assert snap.total == 200
    assert snap.by_camera == {c: 20 for c in cams}
    assert sum(snap.by_zone.values()) == 200 and snap.by_severity == {"low": 200}
    assert snap.version >= 200 and snap.open_alerts_count == 200


def test_record_is_idempotent_by_event_id():
    site = site_id()
    cam = actors.Camera.get(f"{site}-cam")
    e = event(f"{site}-cam", 1)
    first, again = cam.record(e, site), cam.record(e, site)
    assert not first.duplicate and again.duplicate and again.total == 1
    assert actors.Site.get(site).snapshot().total == 1


def test_acknowledge_closes_alert_once():
    site = site_id()
    e = event("cam-x", 1, model=actors.Site.SaesrEvent)
    s = actors.Site.get(site)
    s.record(e)
    assert s.snapshot().open_alerts_count == 1
    acked = s.acknowledge(e.id, "jas")
    assert acked is not None and acked.acknowledged_by == "jas"
    assert s.acknowledge(e.id, "kai") is None
    snap = s.snapshot()
    assert snap.open_alerts_count == 0 and snap.total == 1
    assert snap.recent[0].acknowledged_by == "jas"


def test_summary_matches_formula():
    site = site_id()
    s = actors.Site.get(site)
    for n, sev in enumerate(("high", "medium", "low")):
        s.record(event("cam-s", n, sev, actors.Site.SaesrEvent))
    out = s.summary(24)
    assert out.total == 3 and out.weighted_incidents_per_hour == 9.0 and out.compliance_score == 82.0


def test_camera_status_and_site_camera_health():
    site = site_id()
    cam_id = f"{site}-cam"
    cam = actors.Camera.get(cam_id)
    st = cam.heartbeat(12.5, site)
    assert st.status == "online" and st.fps == 12.5 and st.site == site
    cam.record(event(cam_id, 1), site)
    st = cam.get_status()
    assert st is not None and st.events_seen == 1 and st.last_event is not None
    info = actors.Site.get(site).snapshot().cameras[cam_id]
    assert info.online and info.fps == 12.5


def test_socket_receives_snapshot_on_connect_and_every_change():
    site = site_id()
    s = actors.Site.get(site)
    got: list = []
    ready = threading.Event()

    def listen() -> None:
        with s.connect(actors.Site.Metadata(name="pytest")) as sock:
            for msg in sock:
                got.append(msg)
                ready.set()
                if len(got) >= 3:
                    return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    assert ready.wait(10)
    e = event("cam-w", 1, model=actors.Site.SaesrEvent)
    s.record(e)
    s.acknowledge(e.id, "jas")
    t.join(10)
    assert [m.reason for m in got] == ["connect", "recorded", "acknowledged"]
    assert got[1].snapshot.total == 1 and got[1].event_id == e.id
    assert got[2].snapshot.open_alerts_count == 0
