"""The contract models copied into src/actors.py must match backend/app/schema.py.

Same field names, required fields and defaults, and a full sample round-trips through both to
identical JSON. (The actor copy spells dict[str, Any] as dict[str, JsonValue], so raw JSON
schemas differ only there.)
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import actors  # noqa: E402

spec = importlib.util.spec_from_file_location("backend_schema", ROOT.parent / "backend" / "app" / "schema.py")
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)

MODELS = ["Point", "Event", "TraceEntry", "Action", "Incident", "DetectionMsg", "RelayUpdateMsg", "ActionUpdateMsg",
          "ApproveMsg", "CloseMsg", "HeartbeatMsg", "RelayStart", "CameraState", "SiteState"]


@pytest.mark.parametrize("name", MODELS)
def test_fields_match_backend(name):
    mine, theirs = getattr(actors, name).model_fields, getattr(schema, name).model_fields
    assert list(mine) == list(theirs)
    for f in mine:
        assert mine[f].is_required() == theirs[f].is_required(), f
        if not mine[f].is_required() and mine[f].default_factory is None:
            assert mine[f].default == theirs[f].default, f


def test_full_sample_round_trips_identically():
    ev = {"event_id": "e1", "camera_id": "cam-01", "type": "unsafe_lift", "confidence": 0.9, "timestamp": "t",
          "zone_id": "zone-pack-line", "bbox": [0.1, 0.2, 0.3, 0.4], "centroid": {"x": 0.5, "y": 0.5},
          "metrics": {"torso_angle_deg": 62, "nested": {"a": [1, "b"]}}, "keyframes": ["k"], "clip_url": "c"}
    action = {"action_id": "a1", "incident_id": "i1", "kind": "slow_machine", "status": "approved",
              "params": {"pct": 50}, "approved_by": "jas", "executed_at": "t"}
    inc = {"incident_id": "i1", "camera_id": "cam-01", "type": "unsafe_lift", "zone_id": "zone-pack-line",
           "status": "actioned", "severity": 4, "event_ids": ["e1"], "opened_at": "t", "updated_at": "t",
           "relay": {"triage": {"status": "confirmed", "severity": 4}}, "actions": [action],
           "trace": [{"agent": "triage", "started_at": "t"}], "keyframes": ["k"], "verify_until": "t"}
    site = {"site_id": "site-demo", "site_score": 90.0, "active_incidents": 1, "autonomous_fixes": 1,
            "cameras": [{"camera_id": "cam-01", "status": "recovering", "version": 3, "recovered_from_version": 2,
                         "counters": {"unsafe_lift": 1}, "incidents": [inc]}]}
    start = {"incident_id": "i1", "camera_id": "cam-01", "step": "fixer", "event": ev, "action": action}
    for name, sample in (("SiteState", site), ("RelayStart", start)):
        a = getattr(actors, name).model_validate(sample).model_dump(mode="json")
        b = getattr(schema, name).model_validate(sample).model_dump(mode="json")
        assert a == b
    for msg in ({"kind": "detection", "event": ev},
                {"kind": "relay_update", "incident_id": "i1", "step": "triage", "status": "confirmed",
                 "output": {"severity": 3}, "trace": {"agent": "triage", "started_at": "t"}},
                {"kind": "action_update", "incident_id": "i1", "action": action},
                {"kind": "approve", "incident_id": "i1", "action_id": "a1", "user": "jas"},
                {"kind": "close", "incident_id": "i1"}, {"kind": "heartbeat", "fps": 9.5}):
        a = TypeAdapter(actors.ActorMessage).validate_python(msg).model_dump(mode="json")
        b = TypeAdapter(schema.ActorMessage).validate_python(msg).model_dump(mode="json")
        assert a == b


def test_constants_match():
    assert actors.ACTION_TIER == schema.ACTION_TIER and actors.SITE_ID == schema.SITE_ID
    assert set(actors.STEPS) == set(schema.RelayStep.__args__)
