"""Synthetic keypoint tests for the lift and zone rules. Run: .venv/bin/python -m pytest tests"""
import math

import numpy as np

from app.cv.geometry import angle_from_vertical, joint_angle, measure_lift_pose, point_in_polygon
from app.cv.config import LIFT_SUSTAIN_S
from app.cv.pipeline import Debouncer
from app.cv.rules import CameraConfig, LiftRule, Person, Sustain, Zone, ZoneRule, is_unsafe_lift


def skeleton(back_deg: float, knee_deg: float, hip=(300.0, 300.0), thigh=100.0, shin=100.0, trunk=120.0):
    """Build 17 COCO keypoints: trunk tilted back_deg from vertical, knee interior angle knee_deg."""
    k = np.zeros((17, 2))
    hx, hy = hip
    b = math.radians(back_deg)
    sh = (hx + trunk * math.sin(b), hy - trunk * math.cos(b))
    # knee pushed forward to bend it; thigh tilts by (180 - knee)/2 each way
    bend = math.radians((180.0 - knee_deg) / 2.0)
    knee = (hx + thigh * math.sin(bend), hy + thigh * math.cos(bend))
    ankle = (knee[0] - shin * math.sin(bend), knee[1] + shin * math.cos(bend))
    for i in (5, 6):
        k[i] = sh
    for i in (11, 12):
        k[i] = hip
    for i in (13, 14):
        k[i] = knee
    for i in (15, 16):
        k[i] = ankle
    k[0] = (sh[0] + 10, sh[1] - 20)
    return k


def test_angles():
    assert abs(angle_from_vertical((0, 0), (0, 10))) < 1e-6
    assert abs(angle_from_vertical((10, 10), (0, 10)) - 90) < 1e-6
    assert abs(angle_from_vertical((10, 0), (0, 10)) - 45) < 1e-6
    assert abs(joint_angle((0, 0), (0, 10), (0, 20)) - 180) < 1e-6
    assert abs(joint_angle((10, 10), (0, 10), (0, 20)) - 90) < 1e-6


def test_measure_matches_construction():
    for back, knee in [(10, 175), (62, 170), (60, 100), (80, 160)]:
        p = measure_lift_pose(skeleton(back, knee), np.ones(17), 0.5)
        assert abs(p.back_angle - back) < 0.5, (back, p.back_angle)
        assert abs(p.knee_angle - knee) < 0.5, (knee, p.knee_angle)


def test_classification():
    conf = np.ones(17)
    assert is_unsafe_lift(measure_lift_pose(skeleton(62, 172), conf, 0.5))  # stoop lift
    assert not is_unsafe_lift(measure_lift_pose(skeleton(10, 175), conf, 0.5))  # standing
    assert not is_unsafe_lift(measure_lift_pose(skeleton(55, 95), conf, 0.5))  # squat lift, good form
    assert not is_unsafe_lift(measure_lift_pose(skeleton(35, 175), conf, 0.5))  # slight lean


def test_lying_down_ignored():
    k = skeleton(62, 175)
    # rotate whole body 90 degrees around the hip: legs horizontal
    hip = k[11].copy()
    rot = np.array([[0, -1], [1, 0]])
    k = (k - hip) @ rot.T + hip
    p = measure_lift_pose(k, np.ones(17), 0.5)
    assert not is_unsafe_lift(p)


def test_low_confidence_returns_none():
    conf = np.ones(17)
    conf[[13, 14]] = 0.1  # knees hidden
    assert measure_lift_pose(skeleton(62, 172), conf, 0.5) is None


def test_lift_rule_requires_sustain():
    rule = LiftRule()
    person = Person(1, (0, 0, 400, 600), 0.9, skeleton(62, 172), np.ones(17))
    fps = 25.0
    fired_at = None
    for i in range(30):
        hits, bad = rule([person], i / fps)
        assert 1 in bad
        if hits and fired_at is None:
            fired_at = i / fps
            assert hits[0].type == "unsafe_lift" and hits[0].severity == "high"
            assert "back bent 62 degrees" in hits[0].detail
    assert fired_at is not None and abs(fired_at - LIFT_SUSTAIN_S) < 1.5 / fps


def test_lift_rule_brief_bend_does_not_fire():
    rule = LiftRule()
    bent = Person(1, (0, 0, 400, 600), 0.9, skeleton(62, 172), np.ones(17))
    upright = Person(1, (0, 0, 400, 600), 0.9, skeleton(5, 175), np.ones(17))
    for i in range(40):
        p = bent if (i // 5) % 2 == 0 else upright  # 0.2 s bent, 0.2 s upright
        hits, _ = rule([p], i / 25.0)
        assert not hits


def test_zone_rule():
    assert point_in_polygon((0.5, 0.5), [[0, 0], [1, 0], [1, 1], [0, 1]])
    assert not point_in_polygon((1.5, 0.5), [[0, 0], [1, 0], [1, 1], [0, 1]])
    cfg = CameraConfig("cam-x", "floor", [Zone("lane", True, [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]])])
    rule = ZoneRule(cfg)
    inside = Person(7, (700, 300, 800, 900), 0.8)  # foot (750, 900) on a 1000x1000 frame
    outside = Person(8, (100, 300, 200, 900), 0.8)
    fired = []
    for i in range(20):
        hits, _ = rule([inside, outside], i / 25.0, 1000, 1000)
        fired += hits
    assert fired and all(h.person.track_id == 7 and h.zone == "lane" for h in fired)
    assert rule.zone_of(outside, 1000, 1000) == "floor"


def run_rule(rule, frames, fps=25.0):
    """frames: list of list[Person]. Returns all hits."""
    out = []
    for i, people in enumerate(frames):
        hits, _ = rule(people, i / fps)
        out += hits
    return out


def P(tid, kpts, conf=None, bbox=(0, 0, 400, 600)):
    return Person(tid, bbox, 0.9, kpts, np.ones(17) if conf is None else conf)


def test_upright_never_fires():
    assert not run_rule(LiftRule(), [[P(1, skeleton(5, 178))]] * 100)


def test_correct_squat_never_fires():
    # deep squat with a fairly bent back: knees bent means it is a safe lift
    assert not run_rule(LiftRule(), [[P(1, skeleton(50, 90))]] * 100)
    assert not run_rule(LiftRule(), [[P(1, skeleton(65, 130))]] * 100)


def test_missing_keypoints_never_fire():
    rule = LiftRule()
    k = skeleton(70, 175)
    conf = np.ones(17)
    conf[[15, 16]] = 0.2  # ankles low confidence
    assert not run_rule(rule, [[P(1, k, conf)]] * 100)
    k0 = k.copy()
    k0[[13, 14, 15, 16]] = 0.0  # legs reported at (0, 0): not visible
    assert not run_rule(LiftRule(), [[Person(1, (0, 0, 1, 1), 0.9, k0, None)]] * 100)
    assert not run_rule(LiftRule(), [[Person(1, (0, 0, 1, 1), 0.9, None, None)]] * 100)


def test_half_out_of_frame_never_fires():
    # only upper body visible (legs below the frame edge, low confidence)
    conf = np.ones(17)
    conf[[11, 12, 13, 14, 15, 16]] = 0.05
    assert not run_rule(LiftRule(), [[P(1, skeleton(70, 175), conf)]] * 100)


def test_brief_occlusion_does_not_reset_but_cannot_fire_alone():
    rule = LiftRule()
    conf_hidden = np.ones(17)
    conf_hidden[[13, 14]] = 0.0
    seq = []
    for i in range(25):
        hidden = i in (5, 6)  # 2 frames (80 ms) of hidden knees mid-lift
        seq.append([P(1, skeleton(62, 172), conf_hidden if hidden else None)])
    assert run_rule(rule, seq)  # still fires after 0.5 s
    # occlusion only: never fires
    assert not run_rule(LiftRule(), [[P(1, skeleton(62, 172), conf_hidden)]] * 100)


def test_hysteresis_absorbs_jitter_near_threshold():
    # back oscillates 47/42 deg: below 45 on half the frames, but inside the 5 deg hysteresis band
    seq = [[P(1, skeleton(47 if i % 2 == 0 else 42, 172))] for i in range(25)]
    assert run_rule(LiftRule(), seq)
    # never crossing the entry threshold never fires
    assert not run_rule(LiftRule(), [[P(1, skeleton(42, 172))]] * 50)


def test_multiple_people_only_offender_fires():
    good = P(1, skeleton(5, 178))
    squat = P(2, skeleton(55, 95))
    bad = P(3, skeleton(65, 170))
    hits = run_rule(LiftRule(), [[good, squat, bad]] * 30)
    assert hits and {h.person.track_id for h in hits} == {3}


def test_sustain_states():
    s = Sustain(0.5, 0.25)
    assert not s.update("k", True, 0.0)
    assert not s.update("k", None, 0.1)  # unknown inside the gap keeps the timer
    for t in (0.2, 0.3, 0.4):
        assert not s.update("k", True, t)
    assert s.update("k", True, 0.5)
    assert not s.update("k", False, 0.55)  # explicit False resets at once
    for t in (0.6, 0.7, 0.8, 0.9, 1.0):
        assert not s.update("k", True, t)
    assert s.update("k", True, 1.1)
    assert not s.update("k", True, 1.5)  # dropout longer than the gap restarts the window


def test_debouncer_per_person():
    d = Debouncer(5.0)
    assert d.allow(("unsafe_lift", 1), 0.0)
    assert not d.allow(("unsafe_lift", 1), 4.9)
    assert d.allow(("unsafe_lift", 2), 1.0)  # another person is independent
    assert d.allow(("restricted_zone", 1), 1.0)  # another type is independent
    assert d.allow(("unsafe_lift", 1), 5.1)


def test_polygon_edges():
    tri = [[0, 0], [1, 0], [0, 1]]
    assert point_in_polygon((0.2, 0.2), tri)
    assert not point_in_polygon((0.8, 0.8), tri)
    concave = [[0, 0], [1, 0], [1, 1], [0.5, 0.4], [0, 1]]  # notch at the top middle
    assert point_in_polygon((0.1, 0.8), concave)
    assert not point_in_polygon((0.5, 0.8), concave)
    assert not point_in_polygon((0.5, 0.5), [])  # empty polygon


def test_zone_brief_crossing_does_not_fire():
    cfg = CameraConfig("cam-x", "floor", [Zone("lane", True, [[0.5, 0], [1, 0], [1, 1], [0.5, 1]])])
    rule = ZoneRule(cfg)
    inside = Person(7, (700, 300, 800, 900), 0.8)
    outside = Person(7, (100, 300, 200, 900), 0.8)
    seq = [inside if i % 4 == 0 else outside for i in range(40)]  # 1 frame in, 3 out
    for i, p in enumerate(seq):
        hits, _ = rule([p], i / 25.0, 1000, 1000)
        assert not hits


def test_zones_json_loads():
    from app.cv.rules import load_camera_config

    cfg = load_camera_config("cam-1-loading-dock")
    assert cfg.default_zone == "loading-dock"
    assert any(z.restricted for z in cfg.zones)
    unknown = load_camera_config("cam-9-nowhere")  # falls back to the first camera
    assert unknown.zones
