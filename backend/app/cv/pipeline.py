"""Saesr CV pipeline: camera or video -> pose + tracking + rules -> blurred annotated stream + v2 events.

Server entry point:
    from app.cv.pipeline import run
    await run(publish, set_frame, source, camera_id="cam-01", loop=False)

CLI (no server), events printed to stdout as JSON lines, logs to stderr:
    python -m app.cv.pipeline --source path/to/video.mp4 --camera cam-01 --show
    python -m app.cv.pipeline --source 0
    python -m app.cv.pipeline --source demo.mp4 --loop      # replay mode: loop the file like a live camera
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

import cv2
import numpy as np

from .. import media
from ..schema import SITE_ID, Event, Point, now_iso
from .clips import BufferedFrame, ClipRecorder
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
    PROCESS_FPS,
    SOURCE_RETRY_S,
    TRACKER,
)
from .geometry import SKELETON
from .privacy import blur_faces
from .rules import ComplianceRule, Hit, LiftRule, Person, ZoneRule, load_camera_config

log = logging.getLogger("saesr.cv")

# BGR
RED = (48, 48, 220)
GREEN = (90, 190, 90)
WHITE = (255, 255, 255)
DARK = (25, 25, 25)
FONT = cv2.FONT_HERSHEY_SIMPLEX
LABELS = {"unsafe_lift": "UNSAFE LIFT", "zone_breach": "ZONE BREACH"}

Detector = Callable[[np.ndarray], list[Person]]


class Debouncer:
    """At most one event per key every window_s seconds."""

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


def yolo_detector() -> Detector:
    from ultralytics import YOLO

    model = YOLO(POSE_MODEL)

    def detect(frame: np.ndarray) -> list[Person]:
        res = model.track(
            frame, persist=True, verbose=False, device=DEVICE, conf=DET_CONF, classes=[0], tracker=TRACKER
        )[0]
        return people_from_result(res)

    return detect


class Pipeline:
    """All blocking work lives here; run() calls it through asyncio.to_thread."""

    def __init__(self, source, camera_id: str = DEFAULT_CAMERA, loop: bool = False,
                 detector: Optional[Detector] = None, process_fps: float = PROCESS_FPS):
        self.source = str(source)
        self.camera_id = camera_id
        self.loop = loop
        self.is_file = not self.source.isdigit()
        self.detect = detector or yolo_detector()
        self.cfg = load_camera_config(camera_id)
        self.lift = LiftRule()
        self.zones = ZoneRule(self.cfg)
        self.compliance = ComplianceRule(self.zones)
        self.recorder = ClipRecorder()
        self.interval = 1.0 / max(0.5, process_fps)
        self.cap: Optional[cv2.VideoCapture] = None
        self.fps = 25.0
        self.frames_read = 0  # every decoded frame (drives media time for files)
        self.frames_done = 0  # frames that went through the model
        self.last_t: float = 0.0
        self.next_due: Optional[float] = None
        self.eof = False  # a non-looping file reached its end
        self.t0 = time.monotonic()
        self.infer_ms = 0  # model time for the last processed frame
        self.debounce = Debouncer()
        self.episodes: dict[tuple, float] = {}  # (type, track id, onset) already published  # keyed by (type, track id), timed in media seconds
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
        log.info("opened source %s at %.1f fps (camera %s, loop=%s)", self.source, self.fps, self.camera_id,
                 self.loop)

    def close(self) -> None:
        with self.lock:
            self._release()

    def _release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _read(self) -> Optional[np.ndarray]:
        """One decoded frame, or None. Looping files rewind; a dead webcam is reopened."""
        ok, frame = self.cap.read()
        if (not ok or frame is None) and self.is_file:
            if not self.loop:
                self.eof = True
                return None
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
        self.frames_read += 1
        return frame

    def media_time(self) -> float:
        # Files use the frame count so rules time the same however fast inference runs.
        return self.frames_read / self.fps if self.is_file else time.monotonic() - self.t0

    def read_sampled(self) -> Optional[tuple[np.ndarray, float]]:
        """Next frame due for processing at PROCESS_FPS; frames in between are read and dropped."""
        while True:
            frame = self._read()
            if frame is None:
                return None
            t = self.media_time()
            if self.next_due is None or t >= self.next_due - 1e-6 or t < self.last_t:
                # schedule from the due time, not the frame time, so a 10 fps source still yields ~8 fps
                due = self.next_due if self.next_due is not None and t - self.next_due < self.interval else t
                self.next_due = due + self.interval
                self.last_t = t
                h, w = frame.shape[:2]
                if w > MAX_WIDTH:
                    frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / w)))
                return frame, t

    # ---- one frame ----
    def step(self):
        """Read, infer, blur, apply rules, draw. Returns (annotated, fired, compliant, t) or None.

        fired: debounced violation hits; compliant: compliant_observation hits."""
        with self.lock:
            if self.cap is None:
                return None
            got = self.read_sampled()
        if got is None:
            return None
        frame, t = got
        self.frames_done += 1
        h, w = frame.shape[:2]
        t_inf = time.monotonic()
        people = self.detect(frame)
        self.infer_ms = int(round((time.monotonic() - t_inf) * 1000))
        blur_faces(frame, people)  # privacy first: nothing below ever sees an unblurred face

        lift_hits, lift_bad = self.lift(people, t)
        zone_hits, zone_bad = self.zones(people, t, w, h)
        bad = lift_bad | zone_bad
        hits: list[Hit] = lift_hits + zone_hits
        for hit in hits:
            if hit.zone is None:
                hit.zone = self.zones.zone_of(hit.person, w, h)
        # One event per continuous episode (same onset), and at most one per DEBOUNCE_S per person+type.
        fired = []
        for hit in hits:
            episode = (hit.type, hit.person.track_id, hit.onset_t)
            if episode in self.episodes:
                continue
            if self.debounce.allow((hit.type, hit.person.track_id), t):
                self.episodes[episode] = t
                fired.append(hit)
        if len(self.episodes) > 500:
            self.episodes = {k: v for k, v in self.episodes.items() if t - v < 60.0}
        for hit in fired:  # banner goes on this very frame
            self.banners[LABELS.get(hit.type, hit.type.upper())] = time.monotonic() + BANNER_S
        compliant = self.compliance(people, bad, t, w, h)
        annotated = self.draw(frame, people, bad)
        return annotated, fired, compliant, t

    # ---- overlay ----
    def draw(self, frame: np.ndarray, people: list[Person], bad: set[int]) -> np.ndarray:
        out = frame  # already a private, blurred copy
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
            color = RED if p.track_id in bad else GREEN
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
        _tag(out, f"{self.camera_id}  {datetime.now():%H:%M:%S}", (8, h - int(30 * scale / 0.5)), DARK, scale)
        return out

    def make_event(self, hit: Hit, w: int, h: int) -> Event:
        x1, y1, x2, y2 = hit.person.bbox
        nx1, ny1 = _clamp(x1 / w), _clamp(y1 / h)
        nx2, ny2 = _clamp(x2 / w), _clamp(y2 / h)
        metrics = {k: v for k, v in hit.metrics.items() if v is not None}
        metrics["track_id"] = hit.person.track_id
        metrics["inference_ms"] = self.infer_ms
        return Event(
            site_id=SITE_ID,
            camera_id=self.camera_id,
            type=hit.type,
            confidence=_clamp(float(hit.confidence)),
            timestamp=now_iso(),
            zone_id=hit.zone or self.cfg.default_zone,
            bbox=(round(nx1, 4), round(ny1, 4), round(nx2 - nx1, 4), round(ny2 - ny1, 4)),
            centroid=Point(x=round((nx1 + nx2) / 2, 4), y=round((ny1 + ny2) / 2, 4)),
            metrics=metrics,
        )

    @staticmethod
    def encode(frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return buf.tobytes() if ok else b""


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


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
    publish: Callable[[Event], Awaitable[None]],
    set_frame: Callable[[bytes, str], None],
    source,
    camera_id: str = DEFAULT_CAMERA,
    loop: bool = False,
    *,
    show: bool = False,
    max_frames: Optional[int] = None,
    realtime: bool = True,
    detector: Optional[Detector] = None,
) -> None:
    """Run until cancelled (or the file ends when loop=False, or max_frames processed frames).

    loop=True is replay mode: a video file loops forever as if it were a live camera. Never raises on
    bad frames or a flaky source. Events are published only after their media is written and uploaded.
    """
    while True:
        try:
            pipe = await asyncio.to_thread(Pipeline, source, camera_id, loop, detector)
            break
        except Exception:
            log.exception("could not load pose model %s, retrying", POSE_MODEL)
            await asyncio.sleep(SOURCE_RETRY_S * 5)

    writers: set[asyncio.Task] = set()
    consecutive_errors = 0
    pace: Optional[tuple[float, float]] = None  # (wall, media t) anchor for real-time file playback

    async def deliver(ev: Event, onset_wall: Optional[float], trigger_wall: Optional[float]) -> None:
        now = time.monotonic()
        if onset_wall is not None:
            ev.metrics["latency_ms"] = int(round((now - onset_wall) * 1000))
            if trigger_wall is not None:
                ev.metrics["detect_ms"] = int(round((trigger_wall - onset_wall) * 1000))
        await publish(ev)
        log.info("published %s %s zone=%s conf=%.2f latency_ms=%s detect_ms=%s clip=%s",
                 ev.type, ev.event_id, ev.zone_id, ev.confidence, ev.metrics.get("latency_ms"),
                 ev.metrics.get("detect_ms"), ev.clip_url)

    async def finish_clip(pending) -> None:
        try:
            ev = await asyncio.to_thread(pipe.recorder.write, pending, media.upload)
            await deliver(ev, pending.onset_wall, pending.trigger_wall)
        except Exception:
            log.exception("failed to finish event %s", getattr(pending.event, "event_id", "?"))

    async def finish_still(ev: Event, frame: np.ndarray, onset_wall: float) -> None:
        try:
            ev = await asyncio.to_thread(pipe.recorder.write_still, ev, frame, media.upload)
            await deliver(ev, onset_wall, None)
        except Exception:
            log.exception("failed to publish %s", ev.event_id)

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        writers.add(task)
        task.add_done_callback(writers.discard)

    try:
        while max_frames is None or pipe.frames_done < max_frames:
            if pipe.eof:
                log.info("end of %s after %d processed frames", pipe.source, pipe.frames_done)
                break
            if pipe.cap is None:
                try:
                    await asyncio.to_thread(pipe.open)
                except Exception as exc:
                    log.warning("%s, retrying in %.0f s", exc, SOURCE_RETRY_S)
                    await asyncio.sleep(SOURCE_RETRY_S)
                    continue

            try:
                out = await asyncio.to_thread(pipe.step)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors <= 3 or consecutive_errors % 100 == 0:
                    log.exception("frame %d failed (%d in a row)", pipe.frames_done, consecutive_errors)
                if consecutive_errors % 50 == 0:
                    await asyncio.to_thread(pipe.close)  # force a reopen on the next pass
                await asyncio.sleep(0.05)
                continue
            if out is None:
                await asyncio.sleep(0 if pipe.eof else 0.03)
                continue
            annotated, fired, compliant, t = out
            h, w = annotated.shape[:2]
            now = time.monotonic()

            try:
                set_frame(await asyncio.to_thread(pipe.encode, annotated), pipe.camera_id)
            except Exception:
                log.exception("set_frame failed")

            for pending in pipe.recorder.add_frame(BufferedFrame(t, now_iso(), pipe.camera_id, annotated)):
                spawn(finish_clip(pending))
            for hit in fired:
                onset_wall = now - max(0.0, t - hit.onset_t) if hit.onset_t is not None else now
                ev = pipe.make_event(hit, w, h)
                log.info("%s fired (%s), recording clip for %s", hit.type, hit.detail, ev.event_id)
                pipe.recorder.trigger(ev, t, onset_wall, now)
            for hit in compliant:  # latency here = still write + upload time
                spawn(finish_still(pipe.make_event(hit, w, h), annotated, now))

            if show:
                cv2.imshow("saesr", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if pipe.is_file and realtime:
                # play files at real time so the live view, clips and latency look like a camera
                if pace is None or t < pace[1]:
                    pace = (time.monotonic(), t)
                await asyncio.sleep(max(0.0, pace[0] + (t - pace[1]) - time.monotonic()))
            else:
                await asyncio.sleep(0)

        # normal end: write clips still collecting post-trigger frames
        for pending in pipe.recorder.flush():
            spawn(finish_clip(pending))
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
    ap.add_argument("--camera", default=DEFAULT_CAMERA, help="camera id (cam-01, cam-02), selects zones")
    ap.add_argument("--loop", action="store_true", help="replay mode: loop the file forever like a live camera")
    ap.add_argument("--show", action="store_true", help="open a preview window (q to quit)")
    ap.add_argument("--max-frames", type=int, default=None, help="stop after N processed frames")
    ap.add_argument("--save-frame", default=None, help="write the last annotated frame to this jpg path")
    ap.add_argument("--fast", action="store_true", help="process files as fast as possible (not real time)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    last: dict[str, bytes] = {}

    async def pub(ev: Event) -> None:
        print(ev.model_dump_json(), flush=True)

    def frame(jpeg: bytes, camera_id: str) -> None:
        last["jpeg"] = jpeg

    try:
        asyncio.run(run(pub, frame, args.source, args.camera, args.loop, show=args.show,
                        max_frames=args.max_frames, realtime=not args.fast))
    except KeyboardInterrupt:
        pass
    if args.save_frame and "jpeg" in last:
        with open(args.save_frame, "wb") as f:
            f.write(last["jpeg"])


if __name__ == "__main__":
    _main()
