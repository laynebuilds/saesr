# app.cv: Saesr computer vision pipeline

Camera or video in, annotated live frames plus `SaesrEvent`s out. Two violation types, built to be reliable:

- `unsafe_lift` (high): stoop lift detected from YOLO pose keypoints. Back (shoulder midpoint to hip midpoint) more than 45 degrees from vertical while the worst leg's hip-knee-ankle angle is above 150 degrees (straight legs), legs roughly vertical (not lying down), held for 0.5 s. A correct squat (bent knees) never fires.
- `restricted_zone` (high): the person's foot point (bottom centre of the box) inside a restricted polygon from `zones.json` for 0.3 s.

PPE (hard hat, vest) comes next.

## Entry point

```python
from app.cv.pipeline import run
await run(publish, set_frame, source)  # source: "0" for webcam, or a video file path (looped)
```

`publish(event)` is awaited once per incident, after the clip is written, so `clip_url` (`/clips/<id>.mp4`) and `frame_url` (`/clips/<id>.jpg`) are always set. `set_frame(jpeg_bytes)` is called every frame. The function runs until cancelled: per-frame errors are logged and skipped, a missing or dead source is retried every 2 s, files loop at the end, and cancellation releases the camera.

## CLI

```bash
cd backend
.venv/bin/python -m app.cv.pipeline --source path/to/video.mp4 --show   # preview window, q quits
.venv/bin/python -m app.cv.pipeline --source 0 --camera cam-2-forklift-lane
.venv/bin/python -m app.cv.pipeline --source clip.mp4 --max-frames 300 --save-frame last.jpg
```

Events print to stdout as JSON lines; logs go to stderr.

## Setup

```bash
cd backend
uv venv .venv
uv pip install --python .venv/bin/python ultralytics opencv-python numpy pydantic lap pytest
.venv/bin/python -m pytest tests -q
```

Pose weights (`yolo11n-pose.pt`, 6 MB) download to `backend/` on first run (gitignored).

## Files

- `config.py`: every threshold, the model name, clip timing and paths, each commented. Env overrides: `POSE_MODEL`, `CV_DEVICE` (`mps`, `cpu`), `SAESR_CAMERA`, `ZONES_PATH`, `CLIPS_DIR`.
- `geometry.py`: pure functions (angles, point in polygon, lift posture from keypoints).
- `rules.py`: `LiftRule` and `ZoneRule` with a per-person sustain window (hidden joints are "unknown", never evidence; brief dropouts do not reset; a clearly safe frame resets at once; 5 degree hysteresis once in the window).
- `clips.py`: time-based ring buffer, 4 s before and 2 s after each trigger, written in a worker thread. Tries H.264 (`avc1`, browser playable; works with the macOS opencv-python wheel) and falls back to `mp4v` (needs a transcode to play in `<video>`).
- `pipeline.py`: capture, ultralytics ByteTrack tracking (stable per-person ids, so the 5 s debounce is per person and type), overlay, CLI.
- `zones.json`: per camera `default_zone` plus named polygons in normalized 0..1 coordinates, `restricted: true|false`. Unknown camera ids fall back to the first entry.
