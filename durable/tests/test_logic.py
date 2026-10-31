"""Pure-function tests of the Site logic (no runtime needed)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from actors import (  # noqa: E402
    Incident,
    SaesrEvent,
    acknowledge_incident,
    bump,
    compute_summary,
    insert_incident,
    open_alerts,
    remember_id,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def inc(i: int, severity: str = "high", hours_ago: float = 0.0, camera: str = "cam-1") -> Incident:
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    e = SaesrEvent(id=f"e{i}", camera=camera, type="unsafe_lift", severity=severity, timestamp=ts, zone="dock")
    return Incident(event=e, recorded_at=ts)


def test_empty_summary_is_perfect_score():
    s = compute_summary([], NOW)
    assert s.total == 0 and s.compliance_score == 100.0 and s.window_hours == 1.0 and s.last_event is None


def test_score_matches_backend_formula():
    # One high (5) in a 1h minimum window: 100 - 2*5 = 90.
    assert compute_summary([inc(1)], NOW).compliance_score == 90.0
    # high + medium + low = 9 per hour over 1h: 100 - 18 = 82.
    s = compute_summary([inc(1, "high"), inc(2, "medium"), inc(3, "low")], NOW)
    assert s.weighted_incidents_per_hour == 9.0 and s.compliance_score == 82.0


def test_window_spans_oldest_event_at_least_one_hour():
    # Two highs, oldest 4h ago: 10 weighted / 4h = 2.5 per hour, score 95.
    s = compute_summary([inc(1, hours_ago=4), inc(2)], NOW)
    assert s.window_hours == 4.0 and s.weighted_incidents_per_hour == 2.5 and s.compliance_score == 95.0


def test_score_clamps_to_zero_and_window_cuts_old_events():
    many = [inc(i) for i in range(20)]  # 100 weighted in 1h
    assert compute_summary(many, NOW).compliance_score == 0.0
    s = compute_summary([inc(1, hours_ago=30), inc(2)], NOW, hours=24)
    assert s.total == 1 and s.by_camera == {"cam-1": 1}


def test_summary_counts_and_last_event():
    s = compute_summary([inc(1, camera="a", hours_ago=1), inc(2, camera="b"), inc(3, camera="a", hours_ago=2)], NOW)
    assert s.by_camera == {"a": 2, "b": 1} and s.by_severity == {"high": 3}
    assert s.last_event is not None and s.last_event.id == "e2"


def test_bad_timestamp_is_skipped_not_fatal():
    bad = inc(9)
    bad.event.timestamp = "not a date"
    assert compute_summary([bad, inc(1)], NOW).total == 1


def test_incidents_are_bounded_newest_kept():
    incidents: list[Incident] = []
    for i in range(12):
        insert_incident(incidents, inc(i), limit=10)
    assert len(incidents) == 10 and incidents[0].event.id == "e2" and incidents[-1].event.id == "e11"


def test_seen_ids_bounded():
    seen: list[str] = []
    for i in range(7):
        remember_id(seen, str(i), 5)
    assert seen == ["2", "3", "4", "5", "6"]


def test_acknowledge_is_idempotent_and_closes_alert():
    incidents = [inc(1), inc(2), inc(3)]
    assert [i.event.id for i in open_alerts(incidents)] == ["e3", "e2", "e1"]
    acked = acknowledge_incident(incidents, "e2", "jas", NOW.isoformat())
    assert acked is not None and acked.acknowledged_by == "jas"
    assert acknowledge_incident(incidents, "e2", "kai", NOW.isoformat()) is None  # already closed
    assert acknowledge_incident(incidents, "nope", "kai", NOW.isoformat()) is None
    assert [i.event.id for i in open_alerts(incidents)] == ["e3", "e1"]


def test_bump():
    c: dict[str, int] = {}
    bump(c, "x")
    bump(c, "x")
    assert c == {"x": 2}
