"""Ring buffer and incident clip recorder.

Every processed frame (already face-blurred and annotated) goes into a RING_S second ring buffer
tagged with camera_id and timestamp. When a violation fires, the recorder keeps the CLIP_PRE_S
seconds before the trigger, collects CLIP_POST_S more, then (in a worker thread) writes an H.264
mp4 plus 3 keyframes (before, at, after the trigger), uploads them via app.media.upload, and hands
the event back with clip_url and keyframes set so it can be published.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .config import CLIP_FOURCCS as FOURCCS
from .config import CLIP_POST_S as POST_S
from .config import CLIP_PRE_S as PRE_S
from .config import CLIPS_DIR, KEYFRAME_OFFSET_S, RING_S

log = logging.getLogger("saesr.cv.clips")


@dataclass
class BufferedFrame:
    t: float  # media time (seconds)
    timestamp: str  # ISO UTC wall time
    camera_id: str
    frame: np.ndarray


class RingBuffer:
    """Time based: keeps the last span_s seconds whatever the real fps is."""

    def __init__(self, span_s: float = RING_S):
        self.span_s = span_s
        self.items: deque[BufferedFrame] = deque()

    def append(self, item: BufferedFrame) -> None:
        self.items.append(item)
        while self.items and item.t - self.items[0].t > self.span_s:
            self.items.popleft()

    def since(self, t: float) -> list[BufferedFrame]:
        return [i for i in self.items if i.t >= t]

    def __len__(self) -> int:
        return len(self.items)


@dataclass
class Pending:
    event: object  # app.schema.Event, urls not yet set
    trigger_t: float
    onset_wall: float  # time.monotonic() when the violation condition first became true
    trigger_wall: float  # time.monotonic() when the rule fired
    frames: list = field(default_factory=list)  # BufferedFrame


def nearest(frames: list[BufferedFrame], t: float) -> Optional[BufferedFrame]:
    return min(frames, key=lambda f: abs(f.t - t)) if frames else None


def write_jpg(path: Path, frame: np.ndarray) -> bool:
    return bool(cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88]))


def write_mp4(path: Path, frames: list[BufferedFrame]) -> Optional[str]:
    """Write frames as mp4. Returns the fourcc that worked, or None."""
    if len(frames) < 2:
        return None
    span = frames[-1].t - frames[0].t
    fps = max(2.0, min(60.0, (len(frames) - 1) / span)) if span > 0 else 8.0  # real rate, real speed
    h, w = frames[0].frame.shape[:2]
    for code in FOURCCS:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*code), fps, (w, h))
        if not writer.isOpened():
            writer.release()
            continue
        for f in frames:
            img = f.frame if f.frame.shape[:2] == (h, w) else cv2.resize(f.frame, (w, h))
            writer.write(img)
        writer.release()
        if path.exists() and path.stat().st_size > 0:
            return code
    return None


class ClipRecorder:
    def __init__(self, clips_dir: Path = CLIPS_DIR, ring_s: float = RING_S, pre_s: float = PRE_S,
                 post_s: float = POST_S):
        self.ring = RingBuffer(max(ring_s, pre_s))
        self.pre_s = pre_s
        self.post_s = post_s
        self.pending: list[Pending] = []
        self.dir = Path(clips_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.codec: Optional[str] = None

    def add_frame(self, item: BufferedFrame) -> list[Pending]:
        """Append a processed frame. Returns pendings that now have their post-trigger seconds."""
        self.ring.append(item)
        done = []
        for p in self.pending:
            p.frames.append(item)
            if item.t - p.trigger_t >= self.post_s:
                done.append(p)
        if done:
            self.pending = [p for p in self.pending if p not in done]
        return done

    def trigger(self, event, t: float, onset_wall: float, trigger_wall: float) -> None:
        """Start a clip for event. Call after add_frame for the trigger frame."""
        self.pending.append(
            Pending(event=event, trigger_t=t, onset_wall=onset_wall, trigger_wall=trigger_wall,
                    frames=self.ring.since(t - self.pre_s))
        )

    def flush(self) -> list[Pending]:
        out, self.pending = self.pending, []
        return out

    def write(self, p: Pending, upload: Callable[[Path, str], str]):
        """Blocking (run it in a thread): write + upload mp4 and 3 keyframes, return the updated event."""
        ev = p.event
        keyframes = []
        for i, off in enumerate((-KEYFRAME_OFFSET_S, 0.0, KEYFRAME_OFFSET_S)):
            f = nearest(p.frames, p.trigger_t + off)
            if f is None:
                continue
            path = self.dir / f"{ev.event_id}_k{i}.jpg"
            if write_jpg(path, f.frame):
                keyframes.append(upload(path, "keyframes"))
        clip_path = self.dir / f"{ev.event_id}.mp4"
        code = write_mp4(clip_path, p.frames)
        clip_url = None
        if code is not None:
            self.codec = code
            clip_url = upload(clip_path, "clips")
        else:
            log.warning("clip for %s could not be written", ev.event_id)
        return ev.model_copy(update={"clip_url": clip_url, "keyframes": keyframes})

    def write_still(self, event, frame: np.ndarray, upload: Callable[[Path, str], str]):
        """One keyframe only (compliant observations): cheap evidence, no clip."""
        path = self.dir / f"{event.event_id}_k0.jpg"
        urls = [upload(path, "keyframes")] if write_jpg(path, frame) else []
        return event.model_copy(update={"keyframes": urls})
