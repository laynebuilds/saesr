"""Synthetic keypoint tests for the lift and zone rules. Run: .venv/bin/python -m pytest tests"""
import math

import numpy as np

from app.cv.geometry import angle_from_vertical, joint_angle, measure_lift_pose, point_in_polygon
from app.cv.config import LIFT_SUSTAIN_S, ZONE_DWELL_S
from app.cv.pipeline import Debouncer
from app.cv.rules import (
    CameraConfig, ComplianceRule, LiftRule, Person, Sustain, Zone, ZoneRule, is_unsafe_lift,
)


def skeleton(back_deg: float, knee_deg: float, hip=(300.0, 300.0), thigh=100.0, shin=100.0, trunk=120.0,
             arm=145.0):
    """Build 17 COCO keypoints: trunk tilted back_deg from vertical, knee interior angle knee_deg,
    arms hanging straight down from the shoulders (so a deep stoop puts the wrists near the knees)."""
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
    for i in (9, 10):
        k[i] = (sh[0], sh[1] + arm)
    k[0] = (sh[0] + 10, sh[1] - 20)
    for i in (1, 2, 3, 4):
        k[i] = (sh[0] + 10 + (i - 2.5) * 6, sh[1] - 24)
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
    for i in range(40):
        hits, bad = rule([person], i / fps)
        assert 1 in bad
        if hits and fired_at is None:
            fired_at = i / fps
            assert hits[0].type == "unsafe_lift"
            assert "back bent 62 degrees" in hits[0].detail
            assert abs(hits[0].metrics["torso_angle_deg"] - 62) < 1
            assert hits[0].metrics["duration_s"] >= LIFT_SUSTAIN_S - 1e-6
            assert hits[0].onset_t == 0.0
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
    first = None
    for i in range(40):
        hits, _ = rule([inside, outside], i / 25.0, 1000, 1000)
        if hits and first is None:
            first = i / 25.0
        fired += hits
    assert fired and all(h.person.track_id == 7 and h.zone == "lane" and h.type == "zone_breach" for h in fired)
    assert abs(first - ZONE_DWELL_S) < 1.5 / 25.0  # dwell threshold respected
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
    seq += [[P(1, skeleton(62, 172))]] * 10
    assert run_rule(rule, seq)  # still fires after the sustain window
    # occlusion only: never fires
    assert not run_rule(LiftRule(), [[P(1, skeleton(62, 172), conf_hidden)]] * 100)


def test_hysteresis_absorbs_jitter_near_threshold():
    # back oscillates 47/42 deg: below 45 on half the frames, but inside the 5 deg hysteresis band
    seq = [[P(1, skeleton(47 if i % 2 == 0 else 42, 172))] for i in range(40)]
    assert run_rule(LiftRule(), seq)
    # never crossing the entry threshold never fires
    assert not run_rule(LiftRule(), [[P(1, skeleton(42, 172))]] * 50)


def test_multiple_people_only_offender_fires():
    good = P(1, skeleton(5, 178))
    squat = P(2, skeleton(55, 95))
    bad = P(3, skeleton(65, 170))
    hits = run_rule(LiftRule(), [[good, squat, bad]] * 40)
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
    assert d.allow(("zone_breach", 1), 1.0)  # another type is independent
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

    cfg = load_camera_config("cam-01")
    assert cfg.default_zone == "zone-pack-line"
    assert {z.name for z in cfg.zones} == {"zone-pack-line", "zone-machine-a"}
    assert [z.name for z in cfg.zones if z.restricted] == ["zone-machine-a"]
    cam2 = load_camera_config("cam-02")
    assert cam2.default_zone == "zone-handwash" and not any(z.restricted for z in cam2.zones)
    unknown = load_camera_config("cam-9-nowhere")  # falls back to the first camera
    assert unknown.zones


def test_sustain_is_one_second():
    assert LIFT_SUSTAIN_S == 1.0
    # 0.9 s of a clear stoop lift never fires
    assert not run_rule(LiftRule(), [[P(1, skeleton(62, 172))]] * 22)


def test_wrists_high_never_fires():
    # bent back and straight legs, but hands up near the chest (e.g. leaning to look): not a lift
    assert not run_rule(LiftRule(), [[P(1, skeleton(62, 172, arm=20))]] * 60)
    # wrists hidden: not evidence either
    conf = np.ones(17)
    conf[[9, 10]] = 0.1
    assert not run_rule(LiftRule(), [[P(1, skeleton(62, 172), conf)]] * 60)


def test_wrist_drop_measured():
    p = measure_lift_pose(skeleton(62, 172), np.ones(17), 0.5)
    assert p.wrist_drop is not None and p.wrist_drop >= 0.5
    upright = measure_lift_pose(skeleton(5, 178), np.ones(17), 0.5)
    assert upright.wrist_drop < 0.5


def test_zone_uses_ankles_when_visible():
    # box bottom is outside the zone but the ankle keypoints are inside it
    cfg = CameraConfig("cam-x", "floor", [Zone("lane", True, [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]])])
    k = skeleton(5, 178, hip=(700.0, 500.0))
    person = Person(3, (100, 300, 200, 990), 0.8, k, np.ones(17))
    assert ZoneRule(cfg).zone_of(person, 1000, 1000) == "lane"


def _cam01():
    return CameraConfig("cam-01", "zone-pack-line", [
        Zone("zone-machine-a", True, [[0.62, 0.45], [1, 0.45], [1, 1], [0.62, 1]]),
        Zone("zone-pack-line", False, [[0, 0.45], [0.62, 0.45], [0.62, 1], [0, 1]]),
    ])


def test_compliant_observation_rate_and_zone():
    zr = ZoneRule(_cam01())
    rule = ComplianceRule(zr, interval_s=5.0, min_s=1.0)
    worker = Person(1, (100, 300, 300, 900), 0.85)  # foot (200, 900): pack line
    out = []
    for i in range(int(12 * 8)):  # 12 s at 8 fps
        t = i / 8.0
        out += [(t, h) for h in rule([worker], set(), t, 1000, 1000)]
    assert [round(t, 2) for t, _ in out] == [1.0, 6.0, 11.0]
    assert all(h.type == "compliant_observation" and h.zone == "zone-pack-line" for _, h in out)


def test_compliant_not_emitted_while_violating_or_untracked():
    zr = ZoneRule(_cam01())
    rule = ComplianceRule(zr, interval_s=5.0, min_s=1.0)
    bad_person = Person(1, (100, 300, 300, 900), 0.85)
    untracked = Person(-1, (100, 300, 300, 900), 0.85)
    for i in range(80):
        assert not rule([bad_person, untracked], {1}, i / 8.0, 1000, 1000)
    # once the violation stops, a clean second later it is vouched for again
    hits = []
    for i in range(80, 100):
        hits += rule([bad_person], set(), i / 8.0, 1000, 1000)
    assert len(hits) == 1


def test_compliant_rate_is_per_zone():
    zr = ZoneRule(_cam01())
    rule = ComplianceRule(zr, interval_s=5.0, min_s=0.0)
    a = Person(1, (100, 300, 300, 900), 0.85)  # pack line
    b = Person(2, (300, 300, 500, 900), 0.85)  # also pack line
    c = Person(3, (700, 300, 900, 900), 0.85)  # machine a (not violating yet in this unit test)
    hits = rule([a, b, c], set(), 0.0, 1000, 1000)
    assert sorted(h.zone for h in hits) == ["zone-machine-a", "zone-pack-line"]


# ---- privacy ----

def _noise(h=400, w=400, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_blur_changes_face_only():
    from app.cv.privacy import blur_faces

    img = _noise()
    orig = img.copy()
    k = np.zeros((17, 2))
    k[0:5] = [(200, 100), (195, 95), (205, 95), (190, 98), (210, 98)]
    conf = np.zeros(17)
    conf[0:5] = 0.9
    person = Person(1, (150, 80, 250, 380), 0.9, k, conf)
    (x1, y1, x2, y2), = blur_faces(img, [person])
    assert x1 <= 190 and x2 >= 210 and y1 <= 95 and y2 >= 100  # covers every head keypoint
    face = (slice(y1, y2), slice(x1, x2))
    assert np.mean(img[face] != orig[face]) > 0.9  # face pixels changed
    mask = np.ones(img.shape[:2], bool)
    mask[face] = False
    assert np.array_equal(img[mask], orig[mask])  # everything else untouched


def test_blur_fallback_top_of_box_when_no_head_keypoints():
    from app.cv.config import FACE_FALLBACK_FRAC
    from app.cv.privacy import blur_faces

    img = _noise(seed=1)
    orig = img.copy()
    conf = np.zeros(17)  # nothing confident (turned away)
    person = Person(1, (100, 100, 200, 300), 0.9, np.full((17, 2), 150.0), conf)
    (x1, y1, x2, y2), = blur_faces(img, [person])
    assert (x1, y1, x2) == (100, 100, 200) and y2 == int(np.ceil(100 + 200 * FACE_FALLBACK_FRAC))
    assert np.mean(img[y1:y2, x1:x2] != orig[y1:y2, x1:x2]) > 0.9
    assert np.array_equal(img[y2:, :], orig[y2:, :])
    # no keypoints at all: same fallback
    img2 = _noise(seed=1)
    assert blur_faces(img2, [Person(2, (100, 100, 200, 300), 0.9)]) == [(x1, y1, x2, y2)]


def test_blur_box_clipped_to_frame():
    from app.cv.privacy import blur_faces

    img = _noise(100, 100)
    k = np.zeros((17, 2))
    k[0] = (2, 2)
    conf = np.zeros(17)
    conf[0] = 0.9
    (x1, y1, x2, y2), = blur_faces(img, [Person(1, (0, 0, 60, 100), 0.9, k, conf)])
    assert x1 == 0 and y1 == 0 and x2 <= 100 and y2 <= 100


# ---- end to end: real run() on a generated video with a scripted detector (no model) ----

def test_run_end_to_end(tmp_path, monkeypatch):
    import asyncio

    import cv2

    from app.cv import clips as clips_mod
    from app.cv import pipeline as pl
    from app.schema import Event

    monkeypatch.setattr(clips_mod, "CLIPS_DIR", tmp_path)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    fps, secs, w, h = 8, 24, 320, 240
    src = tmp_path / "src.mp4"
    vw = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(fps * secs):
        vw.write(_noise(h, w, seed=i))
    vw.release()

    calls = {"n": 0}

    def detector(frame):
        calls["n"] += 1
        t = calls["n"] / fps
        if 6.0 <= t < 8.5:
            k = skeleton(62, 172, hip=(100, 150), thigh=40, shin=40, trunk=48, arm=58)  # stoop lift
        elif 13.0 <= t < 21.0:
            k = skeleton(5, 178, hip=(260, 150), thigh=40, shin=40, trunk=48, arm=58)  # in zone-machine-a
        else:
            k = skeleton(5, 178, hip=(100, 150), thigh=40, shin=40, trunk=48, arm=58)  # standing, pack line
        x1, y1 = k.min(axis=0) - 5
        x2, y2 = k.max(axis=0) + 5
        return [Person(4, (x1, y1, x2, y2), 0.9, k, np.ones(17))]

    events: list[Event] = []
    frames: list = []

    published_at = {}

    async def publish(ev):
        events.append(ev)
        published_at[ev.event_id] = len(frames)  # processed frames so far
        if ev.clip_url:  # published before the clip exists: the URL is the final one
            published_at[ev.event_id + "clip_existed"] = (tmp_path / f"{ev.event_id}.mp4").exists()

    def set_frame(jpeg, camera_id):
        frames.append((len(jpeg), camera_id))

    asyncio.run(pl.run(publish, set_frame, str(src), "cam-01", False, realtime=False, detector=detector))

    assert len(frames) == fps * secs and all(c == "cam-01" and n > 0 for n, c in frames)
    by_type = {}
    for ev in events:
        by_type.setdefault(ev.type, []).append(ev)
        assert ev.camera_id == "cam-01" and ev.site_id == "site-demo"
        x, y, bw, bh = ev.bbox
        assert 0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1 and 0 < bh <= 1
        assert 0 <= ev.centroid.x <= 1 and 0 <= ev.centroid.y <= 1
        assert "latency_ms" in ev.metrics
    lift = by_type["unsafe_lift"]
    assert len(lift) == 1
    ev = lift[0]
    assert ev.zone_id == "zone-pack-line"
    assert abs(ev.metrics["torso_angle_deg"] - 62) < 1 and ev.metrics["duration_s"] >= 1.0
    assert ev.clip_url == f"/clips/{ev.event_id}.mp4" and (tmp_path / f"{ev.event_id}.mp4").stat().st_size > 0
    assert len(ev.keyframes) == 3 and all((tmp_path / u.split("/")[-1]).exists() for u in ev.keyframes)
    # published ~0.5 s after the trigger (t = 7.0 s), long before the 5 s post-roll ends (t = 12 s)
    assert published_at[ev.event_id] <= 9 * fps
    assert published_at[ev.event_id + "clip_existed"] is False
    clip = cv2.VideoCapture(str(tmp_path / f"{ev.event_id}.mp4"))
    n = int(clip.get(cv2.CAP_PROP_FRAME_COUNT))
    assert 9 * fps <= n <= 11 * fps + 1  # 5 s before + 5 s after
    zone = by_type["zone_breach"]
    assert len(zone) == 1 and zone[0].zone_id == "zone-machine-a" and zone[0].clip_url
    comp = by_type["compliant_observation"]
    assert len(comp) >= 3 and all(c.clip_url is None and len(c.keyframes) == 1 for c in comp)
    assert {c.zone_id for c in comp} == {"zone-pack-line"}


def test_sampling_hits_target_fps_for_common_sources():
    from app.cv.pipeline import Pipeline

    frame = np.zeros((4, 4, 3), np.uint8)

    class Fake(Pipeline):
        def __init__(self, src_fps):
            self.interval, self.last_t, self.next_due, self.n, self.src = 1 / 8, 0.0, None, 0, src_fps

        def _read(self):
            self.n += 1
            return frame if self.n <= self.src * 10 else None

        def media_time(self):
            return self.n / self.src

    for src, want in ((10, 8.0), (25, 8.0), (30, 8.0), (60, 8.0), (5, 5.0)):
        p = Fake(src)
        n = 0
        while p.read_sampled() is not None:
            n += 1
        assert abs(n / 10 - want) <= 0.2, (src, n)


def test_zone_feet_out_of_frame_is_unknown():
    cfg = CameraConfig("cam-x", "floor", [Zone("lane", True, [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]])])
    rule = ZoneRule(cfg)
    cut = Person(7, (700, 300, 800, 1000), 0.8)  # box cut by the bottom edge, no ankles
    assert not run_zone(rule, [cut] * 60)


def run_zone(rule, people_seq, fps=25.0):
    out = []
    for i, p in enumerate(people_seq):
        hits, _ = rule([p], i / fps, 1000, 1000)
        out += hits
    return out
