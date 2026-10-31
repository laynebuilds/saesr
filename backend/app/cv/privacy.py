"""Face blurring. Applied to every frame before it is drawn on, streamed, buffered or saved.

Face box from the head keypoints (nose, eyes, ears), padded generously. When no head keypoint is
confident (turned away, occluded, no keypoints at all) the top part of the person box is blurred
instead, so a missed keypoint never leaks a face.
"""
from __future__ import annotations

from typing import Iterable, Optional

import cv2
import numpy as np

from .config import FACE_FALLBACK_FRAC, FACE_PAD, KPT_MIN_CONF

HEAD = (0, 1, 2, 3, 4)  # COCO: nose, left eye, right eye, left ear, right ear


def face_box(bbox, kpts: Optional[np.ndarray], kpt_conf: Optional[np.ndarray], w: int, h: int,
             min_conf: float = KPT_MIN_CONF) -> Optional[tuple[int, int, int, int]]:
    """Pixel box (x1, y1, x2, y2) to blur for one person, clipped to the frame, or None if empty."""
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    pts = []
    if kpts is not None:
        for i in HEAD:
            if kpt_conf is not None and kpt_conf[i] < min_conf:
                continue
            if kpts[i][0] == 0 and kpts[i][1] == 0:
                continue
            pts.append(kpts[i])
    if pts:
        arr = np.array(pts, dtype=float)
        cx, cy = arr.mean(axis=0)
        spread = float(max(np.ptp(arr[:, 0]), np.ptp(arr[:, 1])))
        size = max(spread * FACE_PAD, 0.35 * min(bw, bh), 24.0)
        fx1, fy1, fx2, fy2 = cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2
    else:
        fx1, fy1, fx2, fy2 = x1, y1, x2, y1 + bh * FACE_FALLBACK_FRAC
    bx1, by1 = max(0, int(fx1)), max(0, int(fy1))
    bx2, by2 = min(w, int(np.ceil(fx2))), min(h, int(np.ceil(fy2)))
    if bx2 - bx1 < 2 or by2 - by1 < 2:
        return None
    return bx1, by1, bx2, by2


def blur_region(frame: np.ndarray, box: tuple[int, int, int, int]) -> None:
    """Pixelate then smooth the box in place (strong enough that a face is not recognisable)."""
    x1, y1, x2, y2 = box
    roi = frame[y1:y2, x1:x2]
    rh, rw = roi.shape[:2]
    small = cv2.resize(roi, (max(1, rw // 12), max(1, rh // 12)), interpolation=cv2.INTER_AREA)
    roi = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_LINEAR)
    k = max(3, (min(rw, rh) // 4) | 1)
    frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)


def blur_faces(frame: np.ndarray, people: Iterable) -> list[tuple[int, int, int, int]]:
    """Blur every person's face in place. people: objects with bbox, kpts, kpt_conf. Returns boxes."""
    h, w = frame.shape[:2]
    boxes = []
    for p in people:
        box = face_box(p.bbox, p.kpts, p.kpt_conf, w, h)
        if box is not None:
            blur_region(frame, box)
            boxes.append(box)
    return boxes
