# saesr

Computer vision threat detection for warehouses. Cameras you already have, watched by models that flag unsafe lifts, people in machine zones and food-hygiene misses the moment they happen. Every risk is verified by AI agents, fixed end to end, and checked again to confirm the fix worked: detect, triage, coach, fix, verify, report.

## How it fits together

```
camera / video file / replay
      |
      v
backend/app/cv       pose estimation (YOLO11 pose + ByteTrack), stoop-lift and zone rules,
      |              face blur, 5 s + 5 s clips and keyframes uploaded to Supabase Storage
      v
backend/app (FastAPI)  POST /events, /stream/:camera_id (MJPEG), socket grants, admin crash/replay
      |
      v
durable/ (Terse Durable Actors)  one CameraActor per camera + a SiteActor: incident lifecycle,
      |                          dedupe, merge, verification window, idempotent actions, crash recovery
      |   POST /relay/start  <->  POST /internal/cameras/:id/message
      v
relay service (Agent Relay)  triage, coach, fixer, verifier, reporter agents
      |
      v
Supabase           write-through log: events, incidents, actions, relay_trace, corrective_records,
                   batch_holds; Realtime for the dashboard history; Storage for keyframes and clips
      |
      v
dashboard/         live camera state over the actor socket, incident timeline, approvals
```

`MODE=mock` (default) streams realistic fake events from `backend/app/mock.py`. `MODE=live` runs the real pipeline on a webcam or video file, and `POST /admin/replay` loops a recorded clip as a live camera.

## The contract

Shared shapes live in `contract/event.ts` (TypeScript), `contract/event.schema.json` and `backend/app/schema.py`. Change all three together.

```json
{
  "event_id": "8f0c2a64-6a51-4f7e-9d3b-2f4b6c1e9a10",
  "site_id": "site-demo",
  "camera_id": "cam-01",
  "type": "unsafe_lift",
  "confidence": 0.95,
  "timestamp": "2026-10-31T19:47:03.576Z",
  "zone_id": "zone-pack-line",
  "bbox": [0.19, 0.44, 0.38, 0.52],
  "centroid": { "x": 0.38, "y": 0.70 },
  "metrics": { "torso_angle_deg": 74, "knee_angle_deg": 124, "duration_s": 0.88, "latency_ms": 1463 },
  "keyframes": ["https://.../keyframes/site-demo/<id>_k0.jpg", "...k1.jpg", "...k2.jpg"],
  "clip_url": "https://.../clips/site-demo/<id>.mp4"
}
```

Event types: `unsafe_lift`, `zone_breach`, `missing_hairnet`, `missing_gloves`, `compliant_observation`. Incidents, actions, actor messages and the camera/site state broadcast are defined in the same files.

## Who owns what

- **Layne (backend / CV):** the CV pipeline (pose, stoop lift, zones, face blur, clips), the Durable Actors incident lifecycle, the FastAPI service, the Supabase schema and write-through.
- **Jas (frontend / agents):** the dashboard and the Agent Relay service (triage, coach, fixer, verifier, reporter).

## Run it

```bash
# full stack: actors server + API, each in a restart loop (POST /admin/crash brings them back in ~2 s)
./scripts/reset_demo.sh
RELAY_URL=http://127.0.0.1:8100 MODE=off API_EXTRAS="--extra live --extra actors" ./scripts/run_demo.sh

# play a recorded clip as a live camera
curl -XPOST localhost:8000/admin/replay -H 'content-type: application/json' \
  -d '{"camera_id":"cam-01","source":"/path/to/clip.mp4","loop":true}'

# no CV: post test events and run a stand-in relay through a full incident cycle
cd backend && uv run python scripts/mock_events.py --cycle

# dashboard
cd dashboard && npm install && npm run dev
```

See `backend/README.md`, `backend/app/cv/README.md`, `durable/README.md` and `dashboard/README.md` for details.

## Team

- Layne Singh ([@laynebuilds](https://github.com/laynebuilds))
- Jasnoor Kaur ([@jasnoorkaur111](https://github.com/jasnoorkaur111))
