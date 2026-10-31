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
| `RELAY_LOCAL` | unset | `1` turns on the agent layer against the local `npx -y agent-relay@next init` server |
| `RELAY_API_KEY`, `RELAY_BASE_URL` | unset | Agent layer against any Relay workspace (hosted or self-hosted) |
| `OPENAI_API_KEY` | unset | Optional LLM wording for supervisor escalations and briefs (`uv sync --extra agents`) |

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
| GET | `/agents/activity` | Agent messages and supervisor actions (see `app/agents/README.md`) |
| GET | `/agents/stream` | Server-Sent Events of the same items |
| POST | `/agents/ack/{event_id}` | Acknowledge an incident (or `all`) over Relay |

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
- `app/agents/` camera agents and the safety supervisor on Agent Relay (`app/agents/README.md`)


## Reliability

- **Producer supervision:** if the CV pipeline (or mock) crashes, the error is logged and it restarts with backoff (1, 2, 5, 10, 30 s). The API keeps serving.
- **Persistence:** every event is appended to `backend/data/events.jsonl` (override with `EVENTS_LOG`) and replayed on startup, so incidents survive restarts. Corrupt lines are skipped.
- **Back-pressure:** each stream client has a bounded queue (256). A slow client drops its oldest events instead of growing memory.
- **MJPEG:** `/video` only sends a frame when it changed.

## Time windows

- `GET /events/recent?hours=168&limit=500`: incidents from the last week (for the weekly summary).
- `GET /summary?hours=168`: stats over the last week (default 24 h).

## Tests

```bash
cd backend && uv run --group dev pytest -q
```
