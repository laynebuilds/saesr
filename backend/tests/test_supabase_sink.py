import asyncio
import json

import httpx

from app import actors, bus, supabase_sink
from app.schema import Action, CameraState, Event, Incident, TraceEntry
from app.supabase_sink import SupabaseSink, derived_id, event_row, state_rows

T = "2026-10-31T12:00:00Z"


def ev(i, **kw) -> Event:
    base = dict(event_id=f"evt_{i}", camera_id="cam-01", type="unsafe_lift", confidence=0.9, timestamp=T,
                zone_id="zone-pack-line", centroid={"x": 0.4, "y": 0.6}, metrics={"torso_angle_deg": 62},
                keyframes=["k1", "k2", "k3"], clip_url="https://x/clip.mp4")
    base.update(kw)
    return Event(**base)


def state(actions=(), trace=(), status="actioned") -> dict:
    inc = Incident(
        incident_id="inc_1", camera_id="cam-01", type="unsafe_lift", zone_id="zone-pack-line", status=status,
        severity=4, event_ids=["evt_1"], opened_at=T, updated_at="2026-10-31T12:01:00Z",
        relay={"triage": {"verdict": "confirmed", "severity": 4}, "coach": {"message": "bend your knees"}},
        actions=list(actions), trace=list(trace), keyframes=["k1"], clip_url="https://x/clip.mp4",
    )
    return CameraState(camera_id="cam-01", version=5, incidents=[inc]).model_dump(mode="json")


def sink_with(handler) -> SupabaseSink:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseSink("https://x.supabase.co", "sb_secret_test", client, backoff=(0,))


def recorder(status=201):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(status(req) if callable(status) else status)

    return seen, handler


# ---- mappers


def test_event_row_maps_v2_fields():
    row = event_row(ev(1))
    assert row == {
        "event_id": "evt_1", "site_id": "site-demo", "camera_id": "cam-01", "type": "unsafe_lift",
        "confidence": 0.9, "ts": T, "zone_id": "zone-pack-line", "centroid_x": 0.4, "centroid_y": 0.6,
        "metrics": {"torso_angle_deg": 62}, "keyframes": ["k1", "k2", "k3"], "clip_url": "https://x/clip.mp4",
    }
    assert event_row(ev(2, centroid=None))["centroid_x"] is None


def test_state_rows_incident_actions_trace_records_holds():
    actions = [
        Action(action_id="act_coach", incident_id="inc_1", kind="assign_coaching", status="done", executed_at=T),
        Action(action_id="act_cr", incident_id="inc_1", kind="open_corrective_record", status="done",
               params={"description": "Retrain lifting at pack line", "standard_tag": "OSHA 1910"}, executed_at=T),
        Action(action_id="act_hold", incident_id="inc_1", kind="flag_batch_hold", status="done", executed_at=T),
        Action(action_id="act_hold_pending", incident_id="inc_1", kind="flag_batch_hold", status="pending"),
        Action(action_id="act_slow", incident_id="inc_1", kind="slow_machine", status="awaiting_approval",
               params={"machine_id": "machine-a"}),
    ]
    trace = [TraceEntry(agent="triage", model="claude", started_at=T, ended_at=T, output_summary="confirmed")]
    rows = state_rows(state(actions, trace))
    tables = [t for t, _ in rows]
    assert tables[0] == "incidents"
    assert tables.count("actions") == 5 and tables.count("relay_trace") == 1
    assert tables.count("corrective_records") == 1 and tables.count("batch_holds") == 1  # only done ones
    inc = rows[0][1]
    assert inc["id"] == "inc_1" and inc["severity"] == 4 and inc["status"] == "actioned"
    assert inc["triage"] == {"verdict": "confirmed", "severity": 4} and inc["coach"] == {"message": "bend your knees"}
    assert inc["site_id"] == "site-demo" and inc["low_confidence"] is False and inc["keyframes"] == ["k1"]
    cr = next(r for t, r in rows if t == "corrective_records")
    assert cr == {"id": derived_id("cr", "act_cr"), "incident_id": "inc_1", "description": "Retrain lifting at pack line",
                  "standard_tag": "OSHA 1910", "created_at": T}
    hold = next(r for t, r in rows if t == "batch_holds")
    assert hold["id"] == derived_id("bh", "act_hold") and hold["camera_id"] == "cam-01"
    assert hold["window_start"] == "2026-10-31T11:45:00Z" and hold["window_end"] == "2026-10-31T12:15:00Z"
    slow = next(r for t, r in rows if t == "actions" and r["action_id"] == "act_slow")
    assert slow["status"] == "awaiting_approval" and slow["params"] == {"machine_id": "machine-a"}
    assert derived_id("cr", "act_cr") == derived_id("cr", "act_cr") != derived_id("bh", "act_cr")


def test_batch_hold_window_from_params():
    a = Action(action_id="h", incident_id="inc_1", kind="flag_batch_hold", status="done",
               params={"window_start": "2026-10-31T11:00:00Z", "window_end": "2026-10-31T11:30:00Z"})
    hold = next(r for t, r in state_rows(state([a])) if t == "batch_holds")
    assert (hold["window_start"], hold["window_end"]) == ("2026-10-31T11:00:00Z", "2026-10-31T11:30:00Z")


# ---- delivery


def test_upserts_with_merge_duplicates_and_on_conflict_for_trace():
    seen, handler = recorder()
    s = sink_with(handler)
    s.add_event(ev(1))
    s.add_state(state([], [TraceEntry(agent="triage", started_at=T)]))
    asyncio.run(s.drain())
    assert s.sent == 3 and not s.buffer
    paths = [r.url.path for r in seen]
    assert paths == ["/rest/v1/events", "/rest/v1/incidents", "/rest/v1/relay_trace"]
    for r in seen:
        assert r.headers["apikey"] == "sb_secret_test" and r.headers["authorization"] == "Bearer sb_secret_test"
    assert "resolution=merge-duplicates" in seen[0].headers["prefer"] and "on_conflict" not in str(seen[0].url)
    assert "resolution=merge-duplicates" in seen[1].headers["prefer"]
    trace = seen[2]
    assert "resolution=ignore-duplicates" in trace.headers["prefer"]
    assert trace.url.params["on_conflict"] == "incident_id,agent,started_at"
    assert json.loads(seen[1].content)["id"] == "inc_1"


def test_repeated_identical_state_is_not_rewritten_but_changes_are():
    seen, handler = recorder()
    s = sink_with(handler)
    s.add_state(state(status="confirmed"))
    s.add_state(state(status="confirmed"))
    s.add_state(state(status="resolved"))
    asyncio.run(s.drain())
    statuses = [json.loads(r.content)["status"] for r in seen]
    assert statuses == ["confirmed", "resolved"]


def test_outage_is_retried_in_order():
    calls = {"n": 0}
    seen, _ = recorder()

    def handler(req):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("down", request=req)
        seen.append(req)
        return httpx.Response(201)

    s = sink_with(handler)
    s.add_event(ev(1))
    s.add_event(ev(2))
    asyncio.run(s.drain())
    assert [json.loads(r.content)["event_id"] for r in seen] == ["evt_1", "evt_2"] and s.sent == 2


def test_5xx_retried_4xx_given_up():
    seen, handler = recorder(lambda req: 400 if json.loads(req.content)["event_id"] == "evt_bad" else 201)
    s = sink_with(handler)
    s.add_event(ev("bad"))
    s.add_event(ev("good"))
    asyncio.run(s.drain())
    assert s.failed == 1 and s.sent == 1
    assert len([r for r in seen if json.loads(r.content)["event_id"] == "evt_bad"]) == supabase_sink.GIVE_UP_AFTER

    flaky = {"n": 0}

    def h5(req):
        flaky["n"] += 1
        return httpx.Response(503 if flaky["n"] == 1 else 201)

    s2 = sink_with(h5)
    s2.add_event(ev(3))
    asyncio.run(s2.drain())
    assert s2.sent == 1 and s2.failed == 0


def test_buffer_is_bounded(monkeypatch):
    monkeypatch.setattr(supabase_sink, "BUFFER_MAX", 3)
    s = sink_with(recorder()[1])
    for i in range(5):
        s.add_event(ev(i))
    assert len(s.buffer) == 3 and s.dropped_overflow == 2
    assert [r["event_id"] for _, r in s.buffer] == ["evt_2", "evt_3", "evt_4"]


def test_run_pumps_bus_events_and_actor_states(monkeypatch):
    bus.reset()
    states: asyncio.Queue = None  # type: ignore[assignment]
    closed = []

    def open_sub():
        nonlocal states
        states = asyncio.Queue()
        return states

    monkeypatch.setattr(actors, "enabled", lambda: True)
    monkeypatch.setattr(actors, "open_state_subscription", open_sub)
    monkeypatch.setattr(actors, "close_state_subscription", lambda q: closed.append(q))
    seen, handler = recorder()

    async def scenario():
        s = sink_with(handler)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        await bus.publish(ev("live"))
        states.put_nowait(state())
        for _ in range(100):
            if len(seen) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert {r.url.path for r in seen} == {"/rest/v1/events", "/rest/v1/incidents"}
    assert closed  # subscription released on shutdown
    bus.reset()


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert supabase_sink.start() is None
