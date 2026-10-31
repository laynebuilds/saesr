# app.cv: Saesr computer vision pipeline

Camera or video in, face-blurred annotated live frames plus contract v2 `Event`s (`app/schema.py`) out.

| Event | When |
| --- | --- |
| `unsafe_lift` | Stoop lift held 1.0 s: trunk (shoulder mid to hip mid) more than 45 deg from vertical, worst leg hip-knee-ankle above 150 deg (straight legs), legs roughly vertical (not lying or sitting), and wrists low (lowest wrist at or below mid thigh). A squat lift (bent knees) never fires. Hidden joints are "unknown", never evidence. `zone_id` = zone containing the feet, else the camera default. |
| `zone_breach` | Ankle point (or box bottom centre) inside a restricted polygon (`zone-machine-a`) for 1.0 s. A box cut by the bottom edge with no visible ankle is unknown. |
| `compliant_observation` | A tracked person seen in a zone with no violation for 1 s. At most one per camera+zone every 5 s. The verifier relies on these. |

Violations fire once per continuous episode and at most once per 5 s per person and type.
Cameras and zones (`zones.json`, normalized polygons): `cam-01` has `zone-pack-line` (default) and `zone-machine-a` (restricted); `cam-02` has `zone-handwash`.

## Entry point

```python
from app.cv.pipeline import run
await run(publish, set_frame, source, camera_id="cam-01", loop=False)
```

- `source`: `"0"` (webcam) or a video file path. `loop=True` is replay mode: the file loops forever like a live camera (the backup if the webcam or wifi fails). With `loop=False` a file is processed once (in real time) and `run` returns.
- `set_frame(jpeg, camera_id)` is called on every processed frame (blurred, skeleton, boxes, zones, red banner when a violation fires).
- Violations are published 0.5 s after they are confirmed, with 3 `keyframes` (trigger - 2 s from the ring buffer, trigger, trigger + 0.5 s) already written and uploaded, and `clip_url` set to the clip's FINAL URL (`media.final_url`: public Supabase URL when configured, else `/clips/<id>.mp4`). The clip (5 s before + 5 s after, H.264) keeps recording and is written and uploaded in a background thread about 4.5 s later, so the URL can 404 briefly; a failed clip is logged. Compliant observations carry 1 keyframe and no clip, published once it is uploaded.
- `metrics`: `torso_angle_deg`, `knee_angle_deg`, `wrist_drop` (lift), `duration_s`, `track_id`, `inference_ms`, `detect_ms` (condition first true to violation confirmed, ~ the 1 s hold) and `latency_ms` (condition first true to publish: 1.0 s hold + 0.5 s after-keyframe + write; measured 1493 ms on a zone_breach).
- Blocking work runs in threads; per-frame errors are logged and skipped, a dead source is retried.

## CLI

```bash
cd backend
uv run --extra live python -m app.cv.pipeline --source ../samples/unsafe_lift.mp4 --camera cam-01
uv run --extra live python -m app.cv.pipeline --source 0 --camera cam-02 --show      # webcam, preview, q quits
uv run --extra live python -m app.cv.pipeline --source demo.mp4 --loop                # replay mode
uv run --extra live python -m app.cv.pipeline --source demo.mp4 --max-frames 200 --save-frame last.jpg --fast
```

Events print to stdout as JSON lines, logs to stderr. `--fast` processes files as fast as possible instead of real time.

## Privacy

Faces are blurred (pixelate + Gaussian) from the head keypoints (nose, eyes, ears), padded generously, on every frame before it is drawn on, streamed, buffered or saved. No confident head keypoint: the top 25% of the person box is blurred instead. People the detector misses are not blurred.

## Media

`app/media.py` `upload(path, bucket)`: with `SUPABASE_URL` and `SUPABASE_SECRET_KEY` set, uploads to Supabase Storage `{bucket}/site-demo/{file}` (buckets `keyframes`, `clips`) and returns the public URL; otherwise or on any failure returns `/clips/<file>` (served by the API from `backend/clips`).

## Env overrides

`POSE_MODEL`, `CV_DEVICE` (`mps`, `cpu`), `CV_DET_CONF`, `SAESR_CAMERA` (default `cam-01`), `ZONES_PATH`, `CLIPS_DIR`, `CV_PROCESS_FPS` (8), `CV_RING_S` (10), `LIFT_BACK_ANGLE_MIN` (45), `LIFT_KNEE_ANGLE_MIN` (150), `LIFT_LEG_ANGLE_MAX` (35), `LIFT_WRIST_THIGH_FRAC` (0.5), `LIFT_SUSTAIN_S` (1.0), `LIFT_GAP_TOLERANCE_S`, `LIFT_HYSTERESIS_DEG` (5), `KPT_MIN_CONF` (0.5), `ZONE_DWELL_S` (1.0), `CV_DEBOUNCE_S` (5), `COMPLIANT_INTERVAL_S` (5), `COMPLIANT_MIN_S` (1), `CLIP_PRE_S` (5), `CLIP_POST_S` (5), `KEYFRAME_OFFSET_S` (2), `KEYFRAME_AFTER_S` (0.5), `FACE_PAD`, `FACE_FALLBACK_FRAC`. All live in `config.py`.

## Files

- `config.py`: every threshold, with env overrides.
- `geometry.py`: pure functions (angles, point in polygon, lift posture incl. wrist drop).
- `rules.py`: `LiftRule`, `ZoneRule`, `ComplianceRule` with per-person sustain windows (brief dropouts do not reset, a clearly safe frame resets at once, hysteresis once in the window).
- `privacy.py`: face boxes and blur.
- `clips.py`: 10 s ring buffer (frames tagged with camera_id and timestamp), clip and keyframe writer (`avc1` H.264, `mp4v` fallback).
- `pipeline.py`: capture, 8 fps sampling, YOLO11n-pose + ByteTrack, overlay, `run()`, CLI.

Tests: `uv run --extra live --group dev pytest -q tests/test_cv_rules.py tests/test_media.py` (synthetic keypoints, blur, a full `run()` on a generated video with a scripted detector, mocked Supabase).
