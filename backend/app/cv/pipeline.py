"""Saesr CV pipeline: camera or video -> pose + tracking + rules -> annotated stream + incident events.

Server entry point:
    from app.cv.pipeline import run
    await run(publish, set_frame, source)

CLI (no server), events printed to stdout as JSON lines, logs to stderr:
    python -m app.cv.pipeline --source path/to/video.mp4 --show
    python -m app.cv.pipeline --source 0
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import cv2
import numpy as np

from ..schema import SaesrEvent
from .clips import ClipRecorder
from .config import (
    BANNER_S,
    DEBOUNCE_S,
    DEFAULT_CAMERA,
    DET_CONF,
    DEVICE,
    JPEG_QUALITY,
    KPT_MIN_CONF,
    MAX_READ_FAILURES,
    MAX_WIDTH,
    POSE_MODEL,
    SOURCE_RETRY_S,
    TRACKER,
)
from .geometry import SKELETON
from .rules import Hit, LiftRule, Person, ZoneRule, load_camera_config

log = logging.getLogger("saesr.cv")

# BGR
RED = (48, 48, 220)
GREEN = (90, 190, 90)
WHITE = (255, 255, 255)
DARK = (25, 25, 25)
FONT = cv2.FONT_HERSHEY_SIMPLEX
LABELS = {"unsafe_lift": "UNSAFE LIFT", "restricted_zone": "RESTRICTED ZONE"}

_counter = itertools.count(1)


class Debouncer:
    """At most one event per (type, tracked person) every DEBOUNCE_S seconds."""

    def __init__(self, window_s: float = DEBOUNCE_S):
        self.window_s = window_s
        self.last: dict[tuple, float] = {}

    def allow(self, key: tuple, now: float) -> bool:
        if now - self.last.get(key, -1e18) < self.window_s:
            return False
        self.last[key] = now
        return True


def people_from_result(res) -> list[Person]:
    """Convert one ultralytics pose+track result into Person objects."""
    people: list[Person] = []
    boxes = res.boxes
    if boxes is None or len(boxes) == 0:
        return people
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None
    kp = res.keypoints
    kp_xy = kp.xy.cpu().numpy() if kp is not None else None
    kp_cf = kp.conf.cpu().numpy() if kp is not None and kp.conf is not None else None
    for i in range(len(xyxy)):
        people.append(
            Person(
                # untracked boxes (first frames) get ids that never collide with tracker ids
                track_id=int(ids[i]) if ids is not None else -(i + 1),
                bbox=tuple(float(v) for v in xyxy[i]),
                conf=float(confs[i]),
                kpts=kp_xy[i] if kp_xy is not None else None,
                kpt_conf=kp_cf[i] if kp_cf is not None else None,
            )
        )
    return people


class Pipeline:
    """All blocking work lives here; run() calls it through asyncio.to_thread."""

    def __init__(self, source: str, camera: str = DEFAULT_CAMERA):
        from ultralytics import YOLO

        self.source = str(source)
        self.camera = camera
        self.is_file = not self.source.isdigit()
        self.model = YOLO(POSE_MODEL)
        self.cfg = load_camera_config(camera)
        self.lift = LiftRule()
        self.zones = ZoneRule(self.cfg)
        self.recorder = ClipRecorder()
        self.cap: Optional[cv2.VideoCapture] = None
        self.fps = 25.0
        self.frame_no = 0
        self.t0 = time.monotonic()
        self.banners: dict[str, float] = {}  # label -> monotonic time until which it shows
        self.read_failures = 0
        self.lock = threading.Lock()  # step() and close() never overlap (release during read can crash)

    # ---- source ----
    def open(self) -> None:
        with self.lock:
            self._open()

    def _open(self) -> None:
        self._release()
        src = int(self.source) if not self.is_file else self.source
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"could not open video source {self.source!r}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        self.fps = fps if 1 <= fps <= 120 else 25.0
        self.cap = cap
        self.read_failures = 0
        log.info("opened source %s at %.1f fps", self.source, self.fps)

    def close(self) -> None:
        with self.lock:
            self._release()

    def _release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def read(self) -> Optional[np.ndarray]:
        """One frame, or None. Files loop at the end; a dead webcam is reopened."""
        ok, frame = self.cap.read()
        if (not ok or frame is None) and self.is_file:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
            if not ok:
                self._open()
                ok, frame = self.cap.read()
        if not ok or frame is None:
            self.read_failures += 1
            if self.read_failures >= MAX_READ_FAILURES:
                log.warning("source %s stopped delivering frames, reopening", self.source)
                self._open()
            return None
        self.read_failures = 0
        h, w = frame.shape[:2]
        if w > MAX_WIDTH:
            frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / w)))
        return frame

    def media_time(self) -> float:
        # Files use the frame count so rules time the same however fast inference runs.
        return self.frame_no / self.fps if self.is_file else time.monotonic() - self.t0

    # ---- one frame ----
    def step(self):
        """Read, infer, apply rules, draw. Returns (annotated, hits, t) or None if no frame."""
        with self.lock:
            if self.cap is None:
                return None
            frame = self.read()
        if frame is None:
            return None
        self.frame_no += 1
        t = self.media_time()
        h, w = frame.shape[:2]
        res = self.model.track(
            frame, persist=True, verbose=False, device=DEVICE, conf=DET_CONF, classes=[0], tracker=TRACKER
        )[0]
        people = people_from_result(res)

        lift_hits, lift_bad = self.lift(people, t)
        zone_hits, zone_bad = self.zones(people, t, w, h)
        hits: list[Hit] = lift_hits + zone_hits
        for hit in hits:
            if hit.zone is None:
                hit.zone = self.zones.zone_of(hit.person, w, h)
        until = time.monotonic() + BANNER_S
        for hit in hits:
            self.banners[LABELS.get(hit.type, hit.type.upper())] = until
        annotated = self.draw(frame, people, lift_bad | zone_bad)
        return annotated, hits, t

    # ---- overlay ----
    def draw(self, frame: np.ndarray, people: list[Person], bad: set[int]) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]
        scale = max(0.45, min(0.8, w / 1600))
        if self.cfg.zones:
            overlay = out.copy()
            for z in self.cfg.zones:
                if z.restricted:
                    cv2.fillPoly(overlay, [z.pixels(w, h)], RED)
            cv2.addWeighted(overlay, 0.15, out, 0.85, 0, out)
            for z in self.cfg.zones:
                cv2.polylines(out, [z.pixels(w, h)], True, RED if z.restricted else GREEN, 2, cv2.LINE_AA)

        for p in people:
            is_bad = p.track_id in bad
            color = RED if is_bad else GREEN
            x1, y1, x2, y2 = (int(v) for v in p.bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
            if p.kpts is not None:
                vis = (p.kpt_conf >= KPT_MIN_CONF) if p.kpt_conf is not None else np.ones(len(p.kpts), bool)
                for a, b in SKELETON:
                    if vis[a] and vis[b]:
                        cv2.line(out, _pt(p.kpts[a]), _pt(p.kpts[b]), color, 2, cv2.LINE_AA)
                for i in np.flatnonzero(vis):
                    cv2.circle(out, _pt(p.kpts[i]), 3, WHITE, -1, cv2.LINE_AA)
            label = f"#{p.track_id}" if p.track_id >= 0 else "person"
            pose = self.lift.last_pose.get(p.track_id)
            if pose is not None:
                label += f"  back {pose.back_angle:.0f} deg"
            _tag(out, label, (x1, y1 - int(26 * scale / 0.5)), color, scale)

        for z in self.cfg.zones:  # zone names last so people never cover them
            x, y = z.pixels(w, h).min(axis=0)
            _tag(out, z.name, (int(x) + 6, int(y) + 6), RED if z.restricted else GREEN, scale)

        now = time.monotonic()
        active = [lbl for lbl, until in self.banners.items() if until > now]
        if active:
            bh = int(44 * scale / 0.55)
            cv2.rectangle(out, (0, 0), (w, bh), RED, -1)
            cv2.putText(out, "VIOLATION  " + "  |  ".join(active), (14, int(bh * 0.68)), FONT, scale * 1.4,
                        WHITE, 2, cv2.LINE_AA)
        _tag(out, f"{self.camera}  {datetime.now():%H:%M:%S}", (8, h - int(30 * scale / 0.5)), DARK, scale)
        return out

    def make_event(self, hit: Hit) -> SaesrEvent:
        now = datetime.now(timezone.utc)
        parts = self.camera.split("-")
        tag = f"cam{parts[1]}" if len(parts) > 1 and parts[0] == "cam" else self.camera
        x1, y1, x2, y2 = hit.person.bbox
        return SaesrEvent(
            id=f"evt_{now:%Y%m%d_%H%M%S}_{tag}_{next(_counter):04d}",
            camera=self.camera,
            type=hit.type,
            severity=hit.severity,
            timestamp=now.isoformat(),
            zone=hit.zone or self.cfg.default_zone,
            confidence=max(0.0, min(1.0, float(hit.confidence))),
            bbox=(round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)),
            detail=hit.detail,
        )

    @staticmethod
    def encode(frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return buf.tobytes() if ok else b""


def _pt(xy) -> tuple[int, int]:
    return int(xy[0]), int(xy[1])


def _tag(img: np.ndarray, text: str, org: tuple[int, int], bg, scale: float) -> None:
    """Small filled label so text stays readable on any background."""
    (tw, th), base = cv2.getTextSize(text, FONT, scale, 1)
    x = max(0, min(org[0], img.shape[1] - tw - 8))
    y = max(0, min(org[1], img.shape[0] - th - base - 6))
    cv2.rectangle(img, (x, y), (x + tw + 8, y + th + base + 6), bg, -1)
    cv2.putText(img, text, (x + 4, y + th + 3), FONT, scale, WHITE, 1, cv2.LINE_AA)


async def run(
    publish: Callable[[SaesrEvent], Awaitable[None]],
    set_frame: Callable[[bytes], None],
    source: str,
    camera: str = DEFAULT_CAMERA,
    show: bool = False,
    max_frames: Optional[int] = None,
) -> None:
    """Run until cancelled (or max_frames). Never raises on bad frames or a flaky source."""
    while True:
        try:
            pipe = await asyncio.to_thread(Pipeline, str(source), camera)
            break
        except Exception:
            log.exception("could not load pose model %s, retrying", POSE_MODEL)
            await asyncio.sleep(SOURCE_RETRY_S * 5)

    debounce = Debouncer()
    writers: set[asyncio.Task] = set()
    consecutive_errors = 0

    async def finish(pending) -> None:
        try:
            ev = await asyncio.to_thread(pipe.recorder.write, pending)
            if ev.clip_url is None:
                log.warning("clip for %s could not be written", ev.id)
            await publish(ev)
        except Exception:
            log.exception("failed to finish event %s", getattr(pending.event, "id", "?"))

    def spawn(pending) -> None:
        task = asyncio.create_task(finish(pending))
        writers.add(task)
        task.add_done_callback(writers.discard)

    try:
        while max_frames is None or pipe.frame_no < max_frames:
            if pipe.cap is None:
                try:
                    await asyncio.to_thread(pipe.open)
                except Exception as exc:
                    log.warning("%s, retrying in %.0f s", exc, SOURCE_RETRY_S)
                    await asyncio.sleep(SOURCE_RETRY_S)
                    continue

            started = time.monotonic()
            try:
                out = await asyncio.to_thread(pipe.step)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors <= 3 or consecutive_errors % 100 == 0:
                    log.exception("frame %d failed (%d in a row)", pipe.frame_no, consecutive_errors)
                if consecutive_errors % 50 == 0:
                    await asyncio.to_thread(pipe.close)  # force a reopen on the next pass
                await asyncio.sleep(0.05)
                continue
            if out is None:
                await asyncio.sleep(0.03)
                continue
            annotated, hits, t = out

            try:
                set_frame(await asyncio.to_thread(pipe.encode, annotated))
            except Exception:
                log.exception("set_frame failed")

            for pending in pipe.recorder.add_frame(annotated, t):
                spawn(pending)
            now = time.monotonic()
            for hit in hits:
                if debounce.allow((hit.type, hit.person.track_id), now):
                    pipe.recorder.trigger(pipe.make_event(hit), annotated, t)

            if show:
                cv2.imshow("saesr", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if pipe.is_file:
                # play files at real time so the live view and clips look natural
                await asyncio.sleep(max(0.0, 1.0 / pipe.fps - (time.monotonic() - started)))
            else:
                await asyncio.sleep(0)

        # normal end (CLI max_frames or q): write clips still collecting post-trigger frames
        for pending in pipe.recorder.flush():
            spawn(pending)
        if writers:
            await asyncio.gather(*list(writers), return_exceptions=True)
    finally:
        for task in list(writers):
            task.cancel()
        pipe.close()
        if show:
            cv2.destroyAllWindows()


def _main() -> None:
    ap = argparse.ArgumentParser(description="Run the Saesr CV pipeline without the server.")
    ap.add_argument("--source", default="0", help="webcam index (0) or video file path")
    ap.add_argument("--camera", default=DEFAULT_CAMERA, help="camera id, selects zones from zones.json")
    ap.add_argument("--show", action="store_true", help="open a preview window (q to quit)")
    ap.add_argument("--max-frames", type=int, default=None, help="stop after N frames (files loop otherwise)")
    ap.add_argument("--save-frame", default=None, help="write the last annotated frame to this jpg path")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    last: dict[str, bytes] = {}

    async def pub(ev: SaesrEvent) -> None:
        print(ev.model_dump_json(), flush=True)

    def frame(jpeg: bytes) -> None:
        last["jpeg"] = jpeg

    try:
        asyncio.run(run(pub, frame, args.source, args.camera, args.show, args.max_frames))
    except KeyboardInterrupt:
        pass
    if args.save_frame and "jpeg" in last:
        with open(args.save_frame, "wb") as f:
            f.write(last["jpeg"])


if __name__ == "__main__":
    _main()
