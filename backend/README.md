# Saesr backend

FastAPI server for Saesr, computer vision threat detection for warehouses. It streams
safety events (shape: `contract/event.schema.json`) and live annotated frames to the dashboard.

## Run

```
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

Mock mode is the default and needs only the core deps. For the live CV pipeline:

```
cd backend
uv sync --extra live
MODE=live SOURCE=0 uv run uvicorn app.main:app --port 8000
```

## Env vars

| Var | Default | Meaning |
| --- | --- | --- |
| `MODE` | `mock` | `mock` = synthetic events from `app/mock.py`; `live` = `app.cv.pipeline.run(publish, set_frame, SOURCE)` |
| `SOURCE` | `0` | Live mode input: webcam index or video file path |
| `MOCK_EVERY` | `4` | Mock mode: average seconds between events |

## Endpoints

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/health` | `{"ok": true, "mode": "mock"}` |
| GET | `/events/recent?limit=100` | JSON list of recent events (max 500 buffered) |
| GET | `/events/stream` | Server-Sent Events: `data: <event json>` per event, `: keepalive` comment every 15 s |
| GET | `/summary` | Counts by type, zone, camera, severity, plus `compliance_score` 0 to 100 |
| GET | `/frame.jpg` | Latest annotated frame (404 until one exists) |
| GET | `/video` | MJPEG stream (`multipart/x-mixed-replace`) at about 10 fps, usable as an `<img src>` |
| GET | `/clips/...` | Saved incident clips from `backend/clips/` |
| GET | `/samples/...` | Placeholder media from `samples/` (used by mock events) |

CORS is open to all origins. In mock mode the live frame is the sample still of the latest event.

### Compliance score

Severity weights: low 1, medium 3, high 5. Over the last 24 h of buffered events
(window at least 1 h), `score = clamp(100 - 2 * weighted_incidents_per_hour, 0, 100)`.
One high incident per hour costs 10 points.

## Sample media

`samples/` holds a `.jpg` and a 3 s `.mp4` per event type. Regenerate with:

```
cd backend
uv run --with imageio-ffmpeg python scripts/make_samples.py
```

## Layout

- `app/schema.py` event model, mirror of the contract
- `app/bus.py` the only interface between producers and the API
- `app/mock.py` mock event source
- `app/main.py` the API
- `app/cv/` live CV pipeline (separate work)
