"""Violation rules. Each rule consumes per-person observations and returns hits.

Thresholds live in config.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .geometry import LiftPose, measure_lift_pose, point_in_polygon

from .config import (
    COMPLIANT_INTERVAL_S,
    COMPLIANT_MIN_S,
    KPT_MIN_CONF,
    LIFT_BACK_ANGLE_MIN,
    LIFT_GAP_TOLERANCE_S,
    LIFT_HYSTERESIS_DEG,
    LIFT_KNEE_ANGLE_MIN,
    LIFT_LEG_ANGLE_MAX,
    LIFT_SUSTAIN_S,
    LIFT_WRIST_THIGH_FRAC,
    ZONE_DWELL_S,
    ZONE_GAP_TOLERANCE_S,
    ZONES_PATH,
)
from .geometry import L_ANKLE, R_ANKLE


@dataclass
class Person:
    track_id: int
    bbox: tuple[float, float, float, float]  # x1 y1 x2 y2 pixels
    conf: float
    kpts: Optional[np.ndarray] = None  # (17, 2)
    kpt_conf: Optional[np.ndarray] = None  # (17,)

    def _ankles(self) -> list[int]:
        if self.kpts is None:
            return []
        return [
            i for i in (L_ANKLE, R_ANKLE)
            if (self.kpt_conf is None or self.kpt_conf[i] >= KPT_MIN_CONF)
            and not (self.kpts[i][0] == 0 and self.kpts[i][1] == 0)
        ]

    @property
    def foot(self) -> tuple[float, float]:
        """Ankle midpoint when the ankles are visible, else the bottom centre of the box."""
        vis = self._ankles()
        if vis:
            return (
                float(np.mean([self.kpts[i][0] for i in vis])),
                float(np.mean([self.kpts[i][1] for i in vis])),
            )
        x1, _, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)

    def feet_out_of_frame(self, h: int) -> bool:
        """Box cut by the bottom edge and no ankle visible: we do not know where the feet are."""
        return not self._ankles() and self.bbox[3] >= h * 0.98


@dataclass
class Hit:
    type: str
    person: Person
    confidence: float
    detail: str
    zone: Optional[str] = None
    onset_t: Optional[float] = None  # media time the condition first became true
    metrics: dict = field(default_factory=dict)


class Sustain:
    """Per-key sustain window.

    update(key, state, t): state True = condition seen, False = condition clearly absent
    (resets the timer at once), None = unknown this frame (e.g. keypoints hidden). Unknown
    frames are tolerated for gap_s so a brief occlusion does not restart the timer, but they
    never count as evidence on their own. Returns True once the condition has held hold_s.
    """

    def __init__(self, hold_s: float, gap_s: float = 0.25):
        self.hold_s = hold_s
        self.gap_s = gap_s
        self._start: dict = {}
        self._last: dict = {}

    def active(self, key) -> bool:
        return key in self._start

    def start(self, key) -> Optional[float]:
        return self._start.get(key)

    def update(self, key, state: Optional[bool], t: float) -> bool:
        if state is True:
            if key not in self._start or t - self._last[key] > self.gap_s:
                self._start[key] = t
            self._last[key] = t
            return t - self._start[key] >= self.hold_s
        if state is False or (key in self._last and t - self._last[key] > self.gap_s):
            self._start.pop(key, None)
            self._last.pop(key, None)
        return False

    def prune(self, live_keys, t: float) -> None:
        """Forget keys not seen for a while (people who left the frame)."""
        for key in [k for k, last in self._last.items() if k not in live_keys and t - last > 5.0]:
            self._start.pop(key, None)
            self._last.pop(key, None)


def is_unsafe_lift(p: LiftPose, already_active: bool = False) -> bool:
    """Stooped back + straight legs + wrists down low + standing (not lying). Hysteresis: once a
    person is in the violation window the thresholds relax a little so jitter at the boundary does
    not reset the timer. Hidden wrists are not evidence, so they never make a lift unsafe."""
    slack = LIFT_HYSTERESIS_DEG if already_active else 0.0
    wrist_slack = 0.15 if already_active else 0.0
    return (
        p.back_angle > LIFT_BACK_ANGLE_MIN - slack
        and p.knee_angle > LIFT_KNEE_ANGLE_MIN - slack
        and p.leg_angle < LIFT_LEG_ANGLE_MAX
        and p.wrist_drop is not None
        and p.wrist_drop >= LIFT_WRIST_THIGH_FRAC - wrist_slack
    )


class LiftRule:
    def __init__(self):
        self.sustain = Sustain(LIFT_SUSTAIN_S, LIFT_GAP_TOLERANCE_S)
        self.last_pose: dict[int, LiftPose] = {}

    def __call__(self, people: list[Person], t: float) -> tuple[list[Hit], set[int]]:
        """Returns (hits, ids currently in a bad posture, for drawing)."""
        hits, bad = [], set()
        for person in people:
            if person.kpts is None:
                continue
            pose = measure_lift_pose(person.kpts, person.kpt_conf, KPT_MIN_CONF)
            if pose is None:
                # not enough visible joints: unknown, never evidence of a violation
                self.last_pose.pop(person.track_id, None)
                self.sustain.update(person.track_id, None, t)
                continue
            self.last_pose[person.track_id] = pose
            active = is_unsafe_lift(pose, self.sustain.active(person.track_id))
            if active:
                bad.add(person.track_id)
            if self.sustain.update(person.track_id, active, t):
                onset = self.sustain.start(person.track_id)
                # confidence: how far past the threshold, blended with detector confidence
                margin = min(1.0, (pose.back_angle - LIFT_BACK_ANGLE_MIN) / 30.0 + 0.5)
                hits.append(
                    Hit(
                        type="unsafe_lift",
                        person=person,
                        confidence=round(float(max(0.0, min(1.0, 0.5 * person.conf + 0.5 * margin))), 2),
                        detail=f"back bent {pose.back_angle:.0f} degrees with straight legs "
                        f"(knee {pose.knee_angle:.0f} degrees), hands low",
                        onset_t=onset,
                        metrics={
                            "torso_angle_deg": round(pose.back_angle, 1),
                            "knee_angle_deg": round(pose.knee_angle, 1),
                            "wrist_drop": round(float(pose.wrist_drop), 2),
                            "duration_s": round(t - onset, 2) if onset is not None else None,
                        },
                    )
                )
        self.sustain.prune({p.track_id for p in people}, t)
        return hits, bad


@dataclass
class Zone:
    name: str
    restricted: bool
    polygon: list[list[float]]  # normalized 0..1

    def pixels(self, w: int, h: int) -> np.ndarray:
        return np.array([[x * w, y * h] for x, y in self.polygon], dtype=np.int32)

    def contains(self, pt: tuple[float, float], w: int, h: int) -> bool:
        return point_in_polygon((pt[0] / w, pt[1] / h), self.polygon)


@dataclass
class CameraConfig:
    camera: str
    default_zone: str
    zones: list[Zone] = field(default_factory=list)


def load_camera_config(camera: str, path: Path = ZONES_PATH) -> CameraConfig:
    data = {}
    if path.exists():
        data = json.loads(path.read_text())
    cam = data.get(camera)
    if cam is None:
        # fall back to the first camera in the file so a new camera id still gets zones
        cam = next(iter(data.values()), {}) if data else {}
    zones = [Zone(z["name"], bool(z.get("restricted", False)), z["polygon"]) for z in cam.get("zones", [])]
    return CameraConfig(camera=camera, default_zone=cam.get("default_zone", "floor"), zones=zones)


class ZoneRule:
    """zone_breach: the person's foot point inside a restricted polygon for ZONE_DWELL_S."""

    def __init__(self, cfg: CameraConfig, dwell_s: float = ZONE_DWELL_S):
        self.cfg = cfg
        self.sustain = Sustain(dwell_s, ZONE_GAP_TOLERANCE_S)

    def zone_of(self, person: Person, w: int, h: int) -> str:
        for z in self.cfg.zones:
            if z.contains(person.foot, w, h):
                return z.name
        return self.cfg.default_zone

    def __call__(self, people: list[Person], t: float, w: int, h: int) -> tuple[list[Hit], set[int]]:
        hits, bad = [], set()
        for person in people:
            for z in self.cfg.zones:
                if not z.restricted:
                    continue
                key = (person.track_id, z.name)
                if person.feet_out_of_frame(h):
                    self.sustain.update(key, None, t)  # unknown, never evidence
                    continue
                inside = z.contains(person.foot, w, h)
                if inside:
                    bad.add(person.track_id)
                if self.sustain.update(key, inside, t):
                    onset = self.sustain.start(key)
                    hits.append(
                        Hit(
                            type="zone_breach",
                            person=person,
                            confidence=round(float(person.conf), 2),
                            detail=f"person inside restricted area {z.name}",
                            zone=z.name,
                            onset_t=onset,
                            metrics={"duration_s": round(t - onset, 2) if onset is not None else None},
                        )
                    )
        self.sustain.prune({(p.track_id, z.name) for p in people for z in self.cfg.zones}, t)
        return hits, bad


class ComplianceRule:
    """compliant_observation: a tracked person seen in a zone with no violation for COMPLIANT_MIN_S.

    At most one per zone every COMPLIANT_INTERVAL_S (the pipeline is one camera, so zone = camera+zone).
    The verifier relies on these to close incidents, so the only gates are: tracked, clean for a moment.
    """

    def __init__(self, zone_rule: ZoneRule, interval_s: float = COMPLIANT_INTERVAL_S,
                 min_s: float = COMPLIANT_MIN_S):
        self.zone_rule = zone_rule
        self.interval_s = interval_s
        self.min_s = min_s
        self.clean_since: dict[int, float] = {}
        self.last_emit: dict[str, float] = {}

    def __call__(self, people: list[Person], bad: set[int], t: float, w: int, h: int) -> list[Hit]:
        hits = []
        live = set()
        for person in people:
            if person.track_id < 0:
                continue  # untracked detections are not yet a person we can vouch for
            live.add(person.track_id)
            if person.track_id in bad:
                self.clean_since.pop(person.track_id, None)
                continue
            since = self.clean_since.setdefault(person.track_id, t)
            if t - since < self.min_s:
                continue
            zone = self.zone_rule.zone_of(person, w, h)
            if t - self.last_emit.get(zone, -1e18) < self.interval_s:
                continue
            self.last_emit[zone] = t
            hits.append(
                Hit(
                    type="compliant_observation",
                    person=person,
                    confidence=round(float(person.conf), 2),
                    detail=f"person in {zone} with no violation",
                    zone=zone,
                    onset_t=since,
                    metrics={"duration_s": round(t - since, 2)},
                )
            )
        for tid in [k for k in self.clean_since if k not in live]:
            self.clean_since.pop(tid, None)
        return hits
