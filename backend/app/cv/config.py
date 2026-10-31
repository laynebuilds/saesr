"""Every tunable of the CV pipeline in one place. Env vars override the paths and ids."""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# ---- Model ----
# YOLO11 nano pose: 17 COCO keypoints per person, fast enough for real time on a laptop CPU.
# Ultralytics downloads the weights to this path on first run (gitignored via *.pt).
POSE_MODEL = os.environ.get("POSE_MODEL", str(BACKEND_DIR / "yolo11n-pose.pt"))
DEVICE = os.environ.get("CV_DEVICE") or None  # None = ultralytics picks; "mps" for Apple GPU, "cpu", "0"
DET_CONF = 0.4  # person detection confidence floor; lower adds ghosts, higher drops partial people
TRACKER = "bytetrack.yaml"  # ultralytics tracker; gives stable per-person ids across frames
MAX_WIDTH = 1280  # frames wider than this are downscaled before inference (speed, clip size)

# ---- Unsafe lift (stoop lift: bent back with straight legs) ----
LIFT_BACK_ANGLE_MIN = 45.0  # trunk (shoulder mid -> hip mid) angle from vertical above which the back is bent
LIFT_KNEE_ANGLE_MIN = 150.0  # hip-knee-ankle angle above which a leg counts as straight; worst leg is used
LIFT_LEG_ANGLE_MAX = 35.0  # hip -> ankle angle from vertical; above this the person is lying or sitting, ignore
LIFT_SUSTAIN_S = 0.5  # posture must hold this long before it is a violation (kills passing frames)
LIFT_GAP_TOLERANCE_S = 0.25  # joints hidden for less than this do not restart the sustain timer
LIFT_HYSTERESIS_DEG = 5.0  # once inside the window, back/knee thresholds relax by this to absorb jitter
KPT_MIN_CONF = 0.5  # keypoints below this confidence are treated as not visible (never as evidence)

# ---- Restricted zones ----
ZONES_PATH = Path(os.environ.get("ZONES_PATH", Path(__file__).with_name("zones.json")))
ZONE_SUSTAIN_S = 0.3  # foot point must stay inside a restricted polygon this long

# ---- Events ----
DEFAULT_CAMERA = os.environ.get("SAESR_CAMERA", "cam-1-loading-dock")
DEBOUNCE_S = 5.0  # same event type for the same tracked person is published at most once per this

# ---- Clips ----
CLIPS_DIR = Path(os.environ.get("CLIPS_DIR", BACKEND_DIR / "clips"))
CLIP_PRE_S = 4.0  # seconds of history kept before the trigger
CLIP_POST_S = 2.0  # seconds recorded after the trigger; the event is published once the clip is written
CLIP_FOURCCS = ("avc1", "mp4v")  # avc1 (H.264) plays in browsers; mp4v always works but needs a transcode for <video>

# ---- Stream / overlay ----
JPEG_QUALITY = 75  # live MJPEG frame quality
BANNER_S = 2.5  # how long the red violation banner stays on screen
SOURCE_RETRY_S = 2.0  # wait between attempts to (re)open a camera or file
MAX_READ_FAILURES = 30  # consecutive failed webcam reads before the source is reopened
