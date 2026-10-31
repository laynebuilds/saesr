"""Incident clip recorder.

Keeps a ring buffer of the last CLIP_PRE_S seconds of annotated frames. When an event fires it
captures the trigger still, keeps collecting CLIP_POST_S more seconds, then writes
<CLIPS_DIR>/<event id>.mp4 and <event id>.jpg and hands the finished event back so it can be
published with clip_url and frame_url already set.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import CLIP_FOURCCS as FOURCCS
from .config import CLIP_POST_S as POST_S
from .config import CLIP_PRE_S as PRE_S
from .config import CLIPS_DIR


@dataclass
class _Pending:
    event: object  # SaesrEvent
    still: object = None  # annotated trigger frame
    frames: list = field(default_factory=list)  # (t, frame)
    trigger_t: float = 0.0


class ClipRecorder:
    """Time based ring buffer, so clips cover the right seconds whatever the real fps is."""

    def __init__(self, clips_dir: Path = CLIPS_DIR):
        self.buf: deque = deque()  # (t, frame)
        self.pending: list[_Pending] = []
        self.dir = Path(clips_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.codec = None  # fourcc that last worked, for logging

    def add_frame(self, frame: np.ndarray, t: float) -> list[_Pending]:
        """Append an annotated frame at media time t. Returns pendings now complete (ready to write)."""
        self.buf.append((t, frame))
        while self.buf and t - self.buf[0][0] > PRE_S:
            self.buf.popleft()
        done = []
        for p in self.pending:
            p.frames.append((t, frame))
            if t - p.trigger_t >= POST_S:
                done.append(p)
        if done:
            self.pending = [p for p in self.pending if p not in done]
        return done

    def trigger(self, event, still: np.ndarray, t: float) -> None:
        """Start a clip for event. Call after add_frame for the trigger frame."""
        self.pending.append(_Pending(event=event, still=still, frames=list(self.buf), trigger_t=t))

    def flush(self) -> list[_Pending]:
        """Hand back clips still collecting post-trigger frames (used on shutdown)."""
        out, self.pending = self.pending, []
        return out

    def write(self, p: _Pending):
        """Blocking (run it in a thread): write the still + mp4, return the event with urls set."""
        ev = p.event
        still_path = self.dir / f"{ev.id}.jpg"
        still_ok = p.still is not None and cv2.imwrite(str(still_path), p.still, [cv2.IMWRITE_JPEG_QUALITY, 88])
        path = self.dir / f"{ev.id}.mp4"
        if len(p.frames) >= 2:
            span = p.frames[-1][0] - p.frames[0][0]
            # real capture rate so playback runs at real speed
            fps = max(2.0, min(60.0, (len(p.frames) - 1) / span)) if span > 0 else 15.0
            h, w = p.frames[0][1].shape[:2]
            writer = None
            for code in FOURCCS:
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*code), fps, (w, h))
                if writer.isOpened():
                    self.codec = code
                    break
                writer.release()
                writer = None
            if writer is not None:
                for _, f in p.frames:
                    if f.shape[:2] != (h, w):
                        f = cv2.resize(f, (w, h))
                    writer.write(f)
                writer.release()
        clip_ok = path.exists() and path.stat().st_size > 0
        return ev.model_copy(
            update={
                "clip_url": f"/clips/{ev.id}.mp4" if clip_ok else None,
                "frame_url": f"/clips/{ev.id}.jpg" if still_ok else None,
            }
        )
