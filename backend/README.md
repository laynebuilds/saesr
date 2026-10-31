# Saesr backend

FastAPI server for Saesr (contract v2, `app/schema.py`). It ingests detections from the CV
worker, streams them and live annotated frames to the dashboard, forwards every event to its
CameraActor (durable actors, `app/actors.py`), exposes the internal endpoint the relay service
uses to talk back to the actors, and writes everything through to Supabase.

```
CV worker / mock --POST /events or bus.publish--> bus --> SSE /events/stream
                                                     \--> app.actors --> CameraActor --> RELAY_URL/relay/start
relay service --POST /internal/cameras/{id}/message--> CameraActor (incident, actions, trace)
CameraActor state --> dashboard websocket (POST /api/.../socket) and Supabase sink
```

## Run the demo stack

```bash
scripts/run_demo.sh                                   # from the repo root
```

Starts the durable-actors dev server (`cd durable && uv run durable-actors dev`, port 7100) and
the API (uvicorn, port 8000), each in a restart loop, so `POST /admin/crash` brings them back in
about 1 to 2 s. Exports `backend/.env`, sets `ACTORS_URL`, writes `backend/data/actors.pid` and
`backend/data/api.pid`, logs to `backend/data/*.log`, cleans up on Ctrl-C.

| Var (run_demo.sh) | Default | Meaning |
| --- | --- | --- |
| `ACTORS_PORT`, `API_PORT` | `7100`, `8000` | ports |
| `MODE` | `mock` | `live` adds `--extra live` |
| `RELAY_URL` | unset | relay service the actors call (or the stand-in, below) |
| `SKIP_DOTENV` | unset | `1` = do not load `backend/.env` (no Supabase writes) |
| `ACTORS_WATCH` | unset | `1` = reload actor source on change |
| `RESTART_DELAY` | `1` | seconds before a crashed process restarts |

Just the API:

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000                       # mock events, no actors
MODE=live SOURCE=demo.mp4 uv run --extra live uvicorn app.main:app --port 8000
```

## Mock events for the dashboard (no CV)

```bash
cd backend
uv run python scripts/mock_events.py                       # one of each type + a duplicate + compliant
uv run python scripts/mock_events.py --cycle               # full incident cycle, then resolves
uv run python scripts/mock_events.py --cycle --recurring   # repeat violation instead of resolving
uv run python scripts/mock_events.py --base-url http://host:8000
```

`--cycle` runs a STAND-IN relay (testing only; Jas's relay replaces it) on port 8100. Start the
stack with `RELAY_URL=http://127.0.0.1:8100` so the camera actor calls it; otherwise it finds the
incident via `GET /cameras/{id}/state` and drives it anyway. Steps: triage (confirmed, severity 4)
-> coach -> fixer (assign_coaching done, open_corrective_record done, slow_machine
awaiting_approval; flag_batch_hold for hygiene) -> verifier -> reporter, with trace entries, then a
compliant_observation in the same zone. `--hold` keeps it serving so approved actions execute
(calls `/mock/machine/slow`, reports the action done).

## Env vars (API)

| Var | Default | Meaning |
| --- | --- | --- |
| `MODE` | `mock` | `mock` = `app/mock.py` events for cam-01 and cam-02; `live` = `app.cv.pipeline.run` on `SOURCE` |
| `SOURCE` | `0` | live mode input: webcam index or video file |
| `SAESR_CAMERA` | `cam-01` | live mode camera id |
| `MOCK_EVERY` | `4` | mock mode: average seconds between events |
| `ACTORS_URL` | unset | durable actors server; unset = actor routes return 503 |
| `RELAY_URL` | unset | relay service, used by `app/actors.py` |
| `INTERNAL_TOKEN` | unset | if set, `POST /internal/...` needs header `X-Internal-Token` |
| `ADMIN_TOKEN` | unset | if set, `/admin/*` writes need header `X-Admin-Token` |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | unset | Supabase write-through (`app/supabase_sink.py`) |
| `EVENTS_LOG` | `backend/data/events.jsonl` | local event log, replayed on startup |
| `ACTORS_PID_FILE` | `backend/data/actors.pid` | what `/admin/crash?target=actors` kills |
| `AGENTS_SUPERVISOR` | unset | `1` = start the old Agent Relay supervisor (`app/agents`, superseded) |

## Routes

| Method | Path | What |
| --- | --- | --- |
| GET | `/health` | `{"ok": true, "mode": "mock"}` |
| POST | `/events` | one `schema.Event`; 422 on bad input; `{"ok", "event_id", "duplicate"}` (exact event_id duplicates dropped) |
| GET | `/events/recent?limit=&hours=&camera_id=` | recent events, newest last |
| GET | `/events/stream` | SSE `data: <Event json>`, `: keepalive` every 15 s |
| GET | `/summary?hours=24` | violations by type, zone, camera; local `compliance_score` |
| GET | `/cameras` | demo cameras and whether each has a live frame |
| GET | `/frame.jpg?camera_id=` | latest frame (of that camera, or any) |
| GET | `/stream/{camera_id}` | MJPEG of that camera's annotated frames (only changed frames) |
| GET | `/video` | MJPEG of the latest frame of any camera |
| POST | `/internal/cameras/{camera_id}/message` | one `schema.ActorMessage` to the CameraActor; 422 bad, 503 actors off, 502 actor error |
| POST | `/api/cameras/{camera_id}/socket` | websocket grant for that CameraActor |
| POST | `/api/site/socket` | websocket grant for the SiteActor |
| GET | `/cameras/{camera_id}/state` | `CameraState` (503 if actors off) |
| GET | `/site/state` | `SiteState` (503 if actors off) |
| GET | `/actors/status` | actors bridge health |
| POST | `/mock/machine/slow` | simulated machine controller: logs, returns `{"ok": true, "simulated": true}` |
| GET | `/mock/machine/calls` | recent simulated machine calls |
| POST | `/admin/crash?target=api\|actors\|all` | respond, then kill the API and/or the actors dev server on purpose |
| POST | `/admin/replay` | `{camera_id, source, loop: true}`: run the CV pipeline on a file for that camera (503 without `--extra live`) |
| GET | `/admin/replay` | running replays |
| DELETE | `/admin/replay?camera_id=` | stop one (or all) |
| GET | `/clips/...`, `/samples/...` | local media; samples are mock placeholders |
| GET | `/agents/activity`, `/agents/stream`; POST `/agents/ack/{id}` | old Agent Relay supervisor (off by default) |

CORS is open to all origins.

## Supabase

`supabase/schema_v2.sql` (repo root) creates `events`, `incidents`, `relay_trace`, `actions`,
`corrective_records`, `batch_holds` with read-only RLS for anon, Realtime on every table and
public `keyframes` and `clips` buckets. Idempotent; run it in the SQL editor. The sink upserts
(`Prefer: resolution=merge-duplicates`; relay_trace ignores duplicates on
`incident_id,agent,started_at`), buffers up to 2000 writes, retries with backoff and drops a
write after 5 rejections. It never blocks detection or the actors.

## Reliability

- Producer and replays are supervised: a crash is logged and restarted with backoff (1, 2, 5, 10, 30 s).
- Events are appended to `backend/data/events.jsonl` and replayed on startup (bad and v1 lines skipped).
- Each stream client has a bounded queue (256); a slow client drops its oldest events.
- If `app/actors.py` fails to import, the API still starts with actors disabled (logged).

## Tests

```bash
cd backend && uv run --extra live --extra actors --extra agents --group dev pytest -q
```
