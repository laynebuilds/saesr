"""Which actions each incident gets. The policy decides; the model only writes parameters.
This keeps the autonomy bounded and explainable."""
import hashlib

TIER = {"assign_coaching": 0, "open_corrective_record": 0, "flag_batch_hold": 0, "notify_floor_display": 1, "slow_machine": 2}
STANDARD = {"unsafe_lift": "OSHA ergonomics", "zone_breach": "OSHA machine guarding", "missing_hairnet": "GMP", "missing_gloves": "HACCP"}


def allowed_actions(event_type: str, severity: int) -> list[str]:
    if event_type == "unsafe_lift":
        kinds = ["assign_coaching", "notify_floor_display"]
        if severity >= 3:
            kinds.append("open_corrective_record")
        return kinds
    if event_type == "zone_breach":
        kinds = ["notify_floor_display", "open_corrective_record"]
        if severity >= 4:
            kinds.append("slow_machine")  # tier 2: needs a human approval
        return kinds
    if event_type in ("missing_hairnet", "missing_gloves"):
        return ["open_corrective_record", "flag_batch_hold", "assign_coaching", "notify_floor_display"]
    return ["open_corrective_record"]


def action_id(incident_id: str, kind: str) -> str:
    """Deterministic, so a retried fixer call never creates (or runs) an action twice."""
    return hashlib.sha1(f"{incident_id}:{kind}".encode()).hexdigest()[:16]


def default_params(kind: str, event: dict, coach: dict) -> dict:
    zone = str(event.get("zone_id", "")).removeprefix("zone-").replace("-", " ")
    fix = coach.get("recommended_fix") or "Follow the site procedure for this zone."
    return {
        "assign_coaching": {"audience": f"{zone} shift", "topic": fix, "due": "before end of shift"},
        "notify_floor_display": {"display": zone, "message": fix[:80]},
        "open_corrective_record": {"description": f"{event.get('type', 'violation').replace('_', ' ')} in {zone}"},
        "flag_batch_hold": {"window": "10 minutes around the lapse"},
        "slow_machine": {"machine": "machine A", "target_speed": "50%", "duration_s": 60},
    }.get(kind, {})
