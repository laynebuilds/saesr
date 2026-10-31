"""Correlation rules on synthetic event sequences. Pure: no Relay, no clock."""
from datetime import datetime, timedelta, timezone

from app.agents.config import Rules
from app.agents.correlator import Correlator
from app.schema import SaesrEvent

T0 = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
NOW = T0.timestamp()
# Thresholds spelled out here so the tests read on their own.
RULES = Rules(zone_repeat_n=3, zone_repeat_window_s=120, roaming_window_s=90, roaming_min_cameras=2,
              spike_n=6, spike_window_s=60, ack_timeout_s=30, unacked_batch_max=3, unacked_repage_s=30, rule_cooldown_s=90,
              spike_cooldown_s=180)
ZONES = {"cam-1": "loading-dock", "cam-2": "forklift-lane", "cam-3": "packing", "cam-4": "cold-store"}
_n = iter(range(1, 10_000))


def ev(t: float, camera: str = "cam-1", type_: str = "no_vest", severity: str = "low", zone: str | None = None) -> SaesrEvent:
    return SaesrEvent(id=f"evt_{next(_n):04d}", camera=camera, type=type_, severity=severity,
                      timestamp=(T0 + timedelta(seconds=t)).isoformat(), zone=zone or ZONES[camera])


def feed(c: Correlator, *events: SaesrEvent) -> list:
    out = []
    for e in events:
        out += c.observe(e, NOW)
    return out


def rules_fired(findings) -> list[str]:
    return [f.rule for f in findings]


def test_zone_hotspot_fires_on_third_incident_in_window():
    c = Correlator(RULES)
    assert feed(c, ev(0, "cam-1", "unsafe_lift", "high"), ev(30, "cam-1", "no_vest")) == []
    found = feed(c, ev(60, "cam-1", "no_vest"))
    assert rules_fired(found) == ["zone_hotspot"]
    f = found[0]
    assert f.zone == "loading-dock" and len(f.event_ids) == 3
    assert f.severity == "high" and f.escalate  # one of them was high


def test_zone_hotspot_ignores_incidents_outside_window_and_low_only_is_not_escalated():
    c = Correlator(RULES)
    feed(c, ev(0, "cam-3", "no_gloves"), ev(200, "cam-3", "no_gloves"))
    assert feed(c, ev(250, "cam-3", "no_gloves")) == []  # t=0 is outside the 120 s window
    found = feed(c, ev(260, "cam-3", "no_gloves"))
    assert rules_fired(found) == ["zone_hotspot"]
    assert found[0].severity == "medium" and not found[0].escalate


def test_zone_hotspot_cooldown_then_refire():
    c = Correlator(RULES)
    assert rules_fired(feed(c, *(ev(t, "cam-3", "no_gloves") for t in (0, 10, 20)))) == ["zone_hotspot"]
    assert feed(c, ev(30, "cam-3", "no_gloves"), ev(40, "cam-3", "no_gloves")) == []  # cooling down
    assert rules_fired(feed(c, ev(115, "cam-3", "no_gloves"))) == ["zone_hotspot"]  # 95 s after the first


def test_roaming_violator_same_ppe_type_on_two_cameras():
    c = Correlator(RULES)
    assert feed(c, ev(0, "cam-1", "no_hardhat", "medium")) == []
    found = feed(c, ev(40, "cam-2", "no_hardhat", "medium"))
    assert rules_fired(found) == ["roaming_violator"]
    assert found[0].cameras == ["cam-1", "cam-2"] and found[0].escalate
    assert "cam-1 (loading-dock) -> cam-2 (forklift-lane)" in found[0].summary


def test_roaming_needs_ppe_type_and_window():
    c = Correlator(RULES)
    # unsafe_lift does not travel with a person the way missing PPE does
    assert feed(c, ev(0, "cam-1", "unsafe_lift", "high"), ev(10, "cam-2", "unsafe_lift", "high")) == []
    # same PPE type but 100 s apart (window 90 s)
    assert feed(c, ev(0, "cam-1", "no_vest"), ev(100, "cam-3", "no_vest")) == []


def test_shift_spike():
    c = Correlator(RULES)
    cams = ["cam-1", "cam-2", "cam-3", "cam-4", "cam-1"]
    events = [ev(i * 5, cam, "unsafe_lift", "high") for i, cam in enumerate(cams)]
    assert "shift_spike" not in rules_fired(feed(c, *events))
    found = feed(c, ev(30, "cam-2", "unsafe_lift", "high"))
    assert "shift_spike" in rules_fired(found)
    assert "shift_spike" not in rules_fired(feed(c, ev(35, "cam-3", "unsafe_lift", "high")))  # cooldown


def test_unacked_high_escalates_once_after_timeout_in_batches():
    c = Correlator(RULES)
    highs = [ev(i, "cam-2", "restricted_zone", "high") for i in range(4)]
    feed(c, ev(0, "cam-1", "no_vest"), *highs)
    assert c.tick(NOW + 29) == []
    first = c.tick(NOW + 31)
    assert rules_fired(first) == ["unacked_high"]
    assert first[0].event_ids == [e.id for e in highs[:3]]  # batch max 3, oldest first
    assert "4 high severity incidents" in first[0].summary and "plus 1 more" in first[0].summary
    assert c.tick(NOW + 32) == []  # re-page cooldown: the floor manager is not paged every second
    second = c.tick(NOW + 61)
    assert second[0].event_ids == [highs[3].id]
    assert c.tick(NOW + 200) == []  # each incident pages once


def test_ack_prevents_escalation_and_ack_all():
    c = Correlator(RULES)
    a, b, d = (ev(i, "cam-1", "unsafe_lift", "high") for i in range(3))
    feed(c, a, b, d)
    assert c.ack([a.id, "evt_unknown"]) == [a.id]
    overdue = c.tick(NOW + 31)
    assert a.id not in overdue[0].event_ids
    assert sorted(c.ack(["all"])) == sorted([b.id, d.id])
    assert c.open_high == {}


def test_duplicate_event_is_counted_once():
    c = Correlator(RULES)
    e = ev(0, "cam-1", "unsafe_lift", "high")
    feed(c, e, e, e)
    assert sum(c.totals.values()) == 1


def test_snapshot_numbers():
    c = Correlator(RULES)
    feed(c, ev(0, "cam-1", "unsafe_lift", "high"), ev(5, "cam-1", "no_vest"), ev(10, "cam-2", "no_hardhat", "medium"))
    s = c.snapshot(NOW + 10, since=NOW - 100)
    assert s["shift_total"] == 3
    assert s["shift_by_severity"] == {"high": 1, "low": 1, "medium": 1}
    assert s["top_zones"][0] == ("loading-dock", 2)
    assert s["open_unacked_high"] == 1 and s["oldest_unacked_s"] == 10
