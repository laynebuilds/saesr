from app.relay_feed import trace_items


def state(*traces):
    return {"camera_id": "cam-01", "incidents": [{
        "incident_id": "inc-1", "type": "unsafe_lift", "zone_id": "zone-pack-line", "event_ids": ["e1"],
        "trace": list(traces),
    }]}


def test_each_trace_entry_becomes_one_agent_line_once():
    seen = set()
    t1 = {"agent": "triage", "started_at": "t1", "output_summary": "confirmed, severity 4"}
    t2 = {"agent": "coach", "started_at": "t2", "output_summary": None, "error": "timeout"}
    first = trace_items(state(t1), seen)
    assert first == [("triage", "unsafe_lift in zone-pack-line (cam-01): confirmed, severity 4", "e1")]
    second = trace_items(state(t1, t2), seen)  # the next broadcast repeats t1: only t2 is new
    assert second == [("coach", "unsafe_lift in zone-pack-line (cam-01): done (error: timeout)", "e1")]
    assert trace_items(state(t1, t2), seen) == []
