"""Every tunable of the CV pipeline in one place. Most values can be overridden by env vars."""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---- Model ----
# YOLO11 nano pose: 17 COCO keypoints per person, fast enough for real time on a laptop CPU.
# Ultralytics downloads the weights to this path on first run (gitignored via *.pt).
POSE_MODEL = os.environ.get("POSE_MODEL", str(BACKEND_DIR / "yolo11n-pose.pt"))
DEVICE = os.environ.get("CV_DEVICE") or None  # None = ultralytics picks; "mps" for Apple GPU, "cpu", "0"
DET_CONF = _f("CV_DET_CONF", 0.4)  # person detection confidence floor
TRACKER = "bytetrack.yaml"  # ultralytics tracker; gives stable per-person ids across frames
MAX_WIDTH = 1280  # frames wider than this are downscaled before inference (speed, clip size)

# ---- Sampling ----
PROCESS_FPS = _f("CV_PROCESS_FPS", 8.0)  # frames per second sent through the model (5 to 10 is the target)
RING_S = _f("CV_RING_S", 10.0)  # seconds of blurred, annotated frames kept in memory per camera

# ---- Unsafe lift (stoop lift: bent back, straight legs, hands down low) ----
LIFT_BACK_ANGLE_MIN = _f("LIFT_BACK_ANGLE_MIN", 45.0)  # trunk (shoulder mid -> hip mid) angle from vertical
LIFT_KNEE_ANGLE_MIN = _f("LIFT_KNEE_ANGLE_MIN", 150.0)  # hip-knee-ankle angle above which a leg is straight
LIFT_LEG_ANGLE_MAX = _f("LIFT_LEG_ANGLE_MAX", 35.0)  # hip -> ankle from vertical; above this = lying/sitting
# Wrists low: lowest visible wrist at or below this fraction of the way from hip to knee (0.5 = mid thigh).
LIFT_WRIST_THIGH_FRAC = _f("LIFT_WRIST_THIGH_FRAC", 0.5)
LIFT_SUSTAIN_S = _f("LIFT_SUSTAIN_S", 1.0)  # posture must hold this long before it is a violation
LIFT_GAP_TOLERANCE_S = _f("LIFT_GAP_TOLERANCE_S", 0.35)  # hidden joints for less than this keep the timer
LIFT_HYSTERESIS_DEG = _f("LIFT_HYSTERESIS_DEG", 5.0)  # once in the window, thresholds relax by this
KPT_MIN_CONF = _f("KPT_MIN_CONF", 0.5)  # keypoints below this confidence are treated as not visible

# ---- Zones ----
ZONES_PATH = Path(os.environ.get("ZONES_PATH", Path(__file__).with_name("zones.json")))
ZONE_DWELL_S = _f("ZONE_DWELL_S", 1.0)  # foot point must stay inside a restricted polygon this long
ZONE_GAP_TOLERANCE_S = _f("ZONE_GAP_TOLERANCE_S", 0.35)

# ---- Events ----
DEFAULT_CAMERA = os.environ.get("SAESR_CAMERA", "cam-01")
DEBOUNCE_S = _f("CV_DEBOUNCE_S", 5.0)  # same violation type for the same tracked person at most once per this
COMPLIANT_INTERVAL_S = _f("COMPLIANT_INTERVAL_S", 5.0)  # at most one compliant_observation per camera+zone
COMPLIANT_MIN_S = _f("COMPLIANT_MIN_S", 1.0)  # person must be seen without a violation this long first

# ---- Clips ----
CLIPS_DIR = Path(os.environ.get("CLIPS_DIR", BACKEND_DIR / "clips"))
CLIP_PRE_S = _f("CLIP_PRE_S", 5.0)  # seconds of history before the trigger
CLIP_POST_S = _f("CLIP_POST_S", 5.0)  # seconds after the trigger; the event is published once media is up
KEYFRAME_OFFSET_S = _f("KEYFRAME_OFFSET_S", 2.0)  # "before" keyframe at trigger - this (from the ring buffer)
# The event is published this long after the trigger, with the "after" keyframe taken here; the full clip
# (CLIP_PRE_S + CLIP_POST_S) keeps recording and uploads in the background under its final URL.
KEYFRAME_AFTER_S = _f("KEYFRAME_AFTER_S", 0.5)
CLIP_FOURCCS = ("avc1", "mp4v")  # avc1 (H.264) plays in browsers; mp4v always works but needs a transcode

# ---- Privacy ----
FACE_PAD = _f("FACE_PAD", 1.8)  # face box = head keypoint spread times this (generous)
FACE_FALLBACK_FRAC = _f("FACE_FALLBACK_FRAC", 0.25)  # no head keypoints: blur this top fraction of the box

# ---- Stream / overlay ----
JPEG_QUALITY = 75  # live MJPEG frame quality
BANNER_S = 2.5  # how long the red violation banner stays on screen
SOURCE_RETRY_S = 2.0  # wait between attempts to (re)open a camera or file
MAX_READ_FAILURES = 30  # consecutive failed webcam reads before the source is reopened
