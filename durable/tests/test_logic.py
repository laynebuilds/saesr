"""Pure-function tests of the Camera state machine and the Site roll-up (no runtime needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import actors as A  # noqa: E402

T0 = 1_800_000_000_000  # fixed clock, ms


def core() -> A.CameraCore:
    return A.CameraCore(camera_id="cam-01")


def det(eid: str, type_: str = "unsafe_lift", zone: str = "zone-pack-line") -> dict:
    return {
        "kind": "detection",
        "event": {"event_id": eid, "camera_id": "cam-01", "type": type_, "confidence": 0.9, "zone_id": zone,
                  "keyframes": ["k1", "k2", "k3"], "clip_url": "clip.mp4"},
    }


def relay(iid: str, step: str, status: str = "done", **output) -> dict:
    return {"kind": "relay_update", "incident_id": iid, "step": step, "status": status, "output": output}


def act(iid: str, aid: str, kind: str, status: str) -> dict:
    return {"kind": "action_update", "incident_id": iid,
            "action": {"action_id": aid, "incident_id": iid, "kind": kind, "status": status}}


def deliver_all(c: A.CameraCore) -> list[str]:
    ids = [o.id for o in c.outbox]
    for oid in ids:
        A.mark_delivered(c, oid)
    return ids


def to_actioned(c: A.CameraCore, now: int = T0) -> str:
    """Drive one incident through triage -> fixer with one tier-0 action done."""
    A.handle(c, det("e1"), now)
    iid = c.incidents[0].incident_id
    A.handle(c, relay(iid, "triage", "confirmed", severity=4), now)
    A.handle(c, relay(iid, "coach"), now)
    A.handle(c, act(iid, "a1", "assign_coaching", "pending"), now)
    A.handle(c, relay(iid, "fixer"), now)
    A.handle(c, act(iid, "a1", "assign_coaching", "done"), now + 100)
    return iid


# Acceptance 1 ---------------------------------------------------------------


def test_event_creates_one_incident_and_duplicate_is_noop():
    c = core()
    assert A.handle(c, det("e1"), T0)
    v = c.version
    assert not A.handle(c, det("e1"), T0 + 1)  # same event_id twice
    assert len(c.incidents) == 1 and c.version == v and c.counters == {"unsafe_lift": 1}
    inc = c.incidents[0]
    assert inc.status == "verifying" and inc.event_ids == ["e1"] and inc.keyframes == ["k1", "k2", "k3"]
    assert [o.id for o in c.outbox] == [f"{inc.incident_id}:triage"]
    assert c.outbox[0].start.step == "triage" and c.outbox[0].start.event.event_id == "e1"


def test_join_window_and_new_incident():
    c = core()
    A.handle(c, det("e1"), T0)
    A.handle(c, det("e2"), T0 + 5_000)  # joins
    A.handle(c, det("e3", zone="zone-machine-a"), T0 + 6_000)  # other zone: new
    A.handle(c, det("e4"), T0 + 16_000)  # > 10 s after e2: new
    assert [i.event_ids for i in c.incidents] == [["e1", "e2"], ["e3"], ["e4"]]


def test_compliant_observation_never_opens_incident():
    c = core()
    A.handle(c, det("c1", "compliant_observation"), T0)
    assert c.incidents == [] and c.counters == {"compliant_observation": 1} and c.outbox == []


# Relay flow ----------------------------------------------------------------


def test_relay_chain_and_dismissal():
    c = core()
    A.handle(c, det("e1"), T0)
    iid = c.incidents[0].incident_id
    deliver_all(c)
    A.handle(c, relay(iid, "triage", "confirmed", severity=9), T0)
    inc = c.incidents[0]
    assert inc.status == "confirmed" and inc.severity == 5 and [o.start.step for o in c.outbox] == ["coach"]
    A.handle(c, relay(iid, "coach"), T0)
    assert [o.start.step for o in c.outbox] == ["coach", "fixer"]

    d = core()
    A.handle(d, det("x1"), T0)
    did = d.incidents[0].incident_id
    A.handle(d, relay(did, "triage", "dismissed"), T0)
    A.handle(d, relay(did, "triage", "dismissed"), T0)  # resend: no double count
    assert d.incidents[0].status == "dismissed" and d.false_positives_filtered == 1 and d.outbox == []


def test_delivered_step_is_never_requeued():
    c = core()
    A.handle(c, det("e1"), T0)
    iid = c.incidents[0].incident_id
    A.handle(c, relay(iid, "triage", "confirmed"), T0)
    deliver_all(c)
    v = c.version
    assert not A.handle(c, relay(iid, "triage", "confirmed"), T0)  # identical resend: no-op
    assert c.outbox == [] and c.version == v


def test_trace_entries_append_once():
    c = core()
    A.handle(c, det("e1"), T0)
    iid = c.incidents[0].incident_id
    msg = relay(iid, "triage", "confirmed")
    msg["trace"] = {"agent": "triage", "model": "m", "started_at": "t0", "ended_at": "t1", "output_summary": "ok"}
    A.handle(c, msg, T0)
    A.handle(c, msg, T0)
    assert [t.agent for t in c.incidents[0].trace] == ["triage"]


# Actions -------------------------------------------------------------------


def test_action_done_counts_once_and_actioned_opens_window():
    c = core()
    iid = to_actioned(c)
    inc = c.incidents[0]
    assert inc.status == "actioned" and inc.verify_until == A.iso_ms(T0 + 100 + A.VERIFY_WINDOW_MS)
    assert c.autonomous_fixes == 1
    v = c.version
    assert not A.handle(c, act(iid, "a1", "assign_coaching", "done"), T0 + 200)  # resend
    assert not A.handle(c, act(iid, "a1", "assign_coaching", "pending"), T0 + 200)  # cannot reopen
    assert c.autonomous_fixes == 1 and c.version == v


def test_tier2_gate_approve_executes_once():
    c = core()
    A.handle(c, det("e1"), T0)
    iid = c.incidents[0].incident_id
    A.handle(c, relay(iid, "triage", "confirmed"), T0)
    A.handle(c, relay(iid, "fixer"), T0)
    deliver_all(c)
    A.handle(c, act(iid, "s1", "slow_machine", "awaiting_approval"), T0)
    assert c.incidents[0].status == "awaiting_approval"
    # The relay cannot complete a gated action without approval.
    A.handle(c, act(iid, "s1", "slow_machine", "done"), T0)
    assert c.incidents[0].actions[0].status == "awaiting_approval" and c.autonomous_fixes == 0
    A.handle(c, {"kind": "approve", "incident_id": iid, "action_id": "s1", "user": "jas"}, T0)
    a = c.incidents[0].actions[0]
    assert a.status == "approved" and a.approved_by == "jas"
    assert [o.id for o in c.outbox] == [f"{iid}:exec:s1"] and c.outbox[0].start.action.action_id == "s1"
    assert not A.handle(c, {"kind": "approve", "incident_id": iid, "action_id": "s1", "user": "kai"}, T0)
    deliver_all(c)
    A.handle(c, act(iid, "s1", "slow_machine", "done"), T0)
    A.handle(c, act(iid, "s1", "slow_machine", "done"), T0)
    inc = c.incidents[0]
    assert inc.actions[0].approved_by == "jas" and c.autonomous_fixes == 1 and inc.status == "actioned"


def test_close():
    c = core()
    A.handle(c, det("e1"), T0)
    iid = c.incidents[0].incident_id
    A.handle(c, {"kind": "close", "incident_id": iid, "reason": "test"}, T0 + 5)
    assert c.incidents[0].status == "closed" and c.incidents[0].closed_at == A.iso_ms(T0 + 5) and c.outbox == []
    assert not A.handle(c, {"kind": "close", "incident_id": iid}, T0 + 6)


# Acceptance 4: verification window -------------------------------------------


def test_violation_in_window_is_recurring_and_escalates():
    c = core()
    iid = to_actioned(c)
    deliver_all(c)
    A.handle(c, det("e9"), T0 + 5_000)
    inc = c.incidents[0]
    assert len(c.incidents) == 1 and inc.status == "recurring" and inc.event_ids[-1] == "e9"
    assert [o.id for o in c.outbox] == [f"{iid}:fixer:r1"]
    assert c.outbox[0].start.event.metrics["escalation"] == "recurring"
    assert "recurring" in inc.trace[-1].output_summary


def test_compliant_in_window_resolves():
    c = core()
    to_actioned(c)
    A.handle(c, det("ok1", "compliant_observation"), T0 + 5_000)
    inc = c.incidents[0]
    assert inc.status == "resolved" and not inc.low_confidence and inc.verify_until is None


def test_timeout_resolves_low_confidence():
    c = core()
    to_actioned(c)
    deadline = A.next_alarm(c)
    assert deadline == T0 + 100 + A.VERIFY_WINDOW_MS or deadline == c.last_contact_ms + A.OFFLINE_AFTER_MS
    assert A.advance(c, T0 + 100 + A.VERIFY_WINDOW_MS)
    inc = c.incidents[0]
    assert inc.status == "resolved" and inc.low_confidence


def test_compliant_after_window_does_not_resolve_as_compliant():
    c = core()
    to_actioned(c)
    A.handle(c, det("ok1", "compliant_observation"), T0 + 100 + A.VERIFY_WINDOW_MS + 1)
    assert c.incidents[0].low_confidence  # timeout applied first


# Heartbeat, offline, recovery --------------------------------------------------


def test_heartbeat_and_offline():
    c = core()
    A.handle(c, {"kind": "heartbeat", "fps": 12}, T0)
    assert c.status == "online" and c.last_heartbeat == A.iso_ms(T0)
    assert A.next_alarm(c) == T0 + A.OFFLINE_AFTER_MS
    A.advance(c, T0 + A.OFFLINE_AFTER_MS)
    assert c.status == "offline" and A.next_alarm(c) is None
    A.handle(c, {"kind": "heartbeat"}, T0 + A.OFFLINE_AFTER_MS + 1)
    assert c.status == "online"


def test_recover_marks_recovering_then_online():
    c = core()
    A.handle(c, det("e1"), T0, boot_id="boot-a")
    A.handle(c, {"kind": "heartbeat"}, T0, boot_id="boot-a")
    v = c.version
    assert c.recovered_from_version is None
    A.advance(c, T0 + 500, boot_id="boot-b")  # new process
    assert c.status == "recovering" and c.recovered_from_version == v and c.version == v + 1
    assert len(c.incidents) == 1
    A.handle(c, {"kind": "heartbeat"}, T0 + 600, boot_id="boot-b")
    assert c.status == "recovering"  # stays visible for RECOVERING_MS
    A.advance(c, T0 + 500 + A.RECOVERING_MS, boot_id="boot-b")
    assert c.status == "online"


def test_first_boot_is_not_a_recovery():
    c = core()
    A.handle(c, det("e1"), T0, boot_id="boot-a")
    assert c.recovered_from_version is None and c.status == "online"


# Score and roll-up ---------------------------------------------------------------


def test_compliance_score():
    c = core()
    assert A.to_state(c, T0).compliance_score == 100.0
    A.handle(c, det("e1"), T0)
    assert A.to_state(c, T0).compliance_score == 88.0  # open, severity unknown (3): 4*3
    iid = to_actioned(core())
    c2 = core()
    to_actioned(c2)
    assert A.to_state(c2, T0).compliance_score == 92.0  # actioned, severity 4: 2*4
    A.handle(c2, det("ok", "compliant_observation"), T0 + 1_000)
    assert A.to_state(c2, T0 + 1_000).compliance_score == 92.0
    assert A.to_state(c2, T0 + 1_000 + A.SCORE_RECOVERY_MS).compliance_score == 100.0
    assert iid


def test_rollup():
    a, b = core(), A.CameraCore(camera_id="cam-02")
    to_actioned(a)
    A.handle(b, det("z", zone="zone-handwash", type_="missing_gloves"), T0)
    site = A.rollup([A.to_state(b, T0), A.to_state(a, T0)])
    assert [c.camera_id for c in site.cameras] == ["cam-01", "cam-02"]
    assert site.active_incidents == 2 and site.autonomous_fixes == 1 and site.site_score == 90.0


def test_incidents_bounded():
    c = core()
    for i in range(A.INCIDENTS_MAX + 5):
        A.handle(c, det(f"e{i}"), T0 + i * 20_000)
    assert len(c.incidents) == A.INCIDENTS_MAX


def test_seen_ids_bounded():
    items: list[str] = []
    for i in range(7):
        A._remember(items, str(i), 5)
    assert items == ["2", "3", "4", "5", "6"]
