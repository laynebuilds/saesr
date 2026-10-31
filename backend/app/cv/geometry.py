"""Pure geometry helpers for pose based rules. No model, no OpenCV, easy to unit test."""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

# COCO 17 keypoint indices (what YOLO pose outputs)
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
    (0, 5), (0, 6),
]


def angle_from_vertical(top: Sequence[float], bottom: Sequence[float]) -> float:
    """Angle in degrees between the vector bottom->top and straight up (image y grows down).

    0 = perfectly upright, 90 = horizontal.
    """
    dx = top[0] - bottom[0]
    dy = bottom[1] - top[1]  # positive when top is above bottom
    return math.degrees(math.atan2(abs(dx), dy))


def joint_angle(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    """Interior angle at b in degrees for the chain a-b-c. 180 = straight."""
    v1 = np.array([a[0] - b[0], a[1] - b[1]], dtype=float)
    v2 = np.array([c[0] - b[0], c[1] - b[1]], dtype=float)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return math.degrees(math.acos(cos))


def midpoint(p: Sequence[float], q: Sequence[float]) -> tuple[float, float]:
    return ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)


def point_in_polygon(pt: Sequence[float], poly: Sequence[Sequence[float]]) -> bool:
    """Ray casting. pt and poly in the same coordinate space."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


class LiftPose:
    """Measured lift posture of one person in one frame."""

    __slots__ = ("back_angle", "knee_angle", "leg_angle")

    def __init__(self, back_angle: float, knee_angle: float, leg_angle: float):
        self.back_angle = back_angle  # trunk angle from vertical
        self.knee_angle = knee_angle  # hip-knee-ankle interior angle (worst visible leg)
        self.leg_angle = leg_angle  # hip->ankle angle from vertical (rejects lying down)


def measure_lift_pose(kpts: np.ndarray, conf: Optional[np.ndarray], min_conf: float) -> Optional[LiftPose]:
    """kpts: (17, 2) pixel coords, conf: (17,) or None. Returns None if not enough visible joints."""

    def ok(i: int) -> bool:
        if conf is not None and conf[i] < min_conf:
            return False
        return not (kpts[i][0] == 0 and kpts[i][1] == 0)

    shoulders = [i for i in (L_SHOULDER, R_SHOULDER) if ok(i)]
    hips = [i for i in (L_HIP, R_HIP) if ok(i)]
    if not shoulders or not hips:
        return None
    sh = np.mean([kpts[i] for i in shoulders], axis=0)
    hp = np.mean([kpts[i] for i in hips], axis=0)
    back = angle_from_vertical(sh, hp)

    knee_angles = []
    leg_angles = []
    for hip, knee, ankle in ((L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE)):
        if ok(hip) and ok(knee) and ok(ankle):
            knee_angles.append(joint_angle(kpts[hip], kpts[knee], kpts[ankle]))
            leg_angles.append(angle_from_vertical(kpts[hip], kpts[ankle]))
    if not knee_angles:
        return None
    # Worst (most bent) leg: if either knee is bent it is not a straight-leg stoop.
    return LiftPose(back, min(knee_angles), max(leg_angles))
