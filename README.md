# saesr

Computer vision threat detection for warehouses. Cameras you already have, watched by models that flag unsafe lifts, missing PPE and people in restricted zones the moment it happens, with a clip of every incident and a weekly summary of the top risks.

## How we are judged (every decision maps to one of these, 20% each)

| Criterion | What it means for saesr |
| --- | --- |
| **Use of real-time AI and track technology** | Detection runs live on the camera feed; incidents stream in under a second. Agent Relay: camera agents and a safety supervisor coordinate directly. Terse Durable Actors: live warehouse state the dashboard subscribes to. |
| **Originality and creativity** | Not another dashboard of boxes: agents that reason across cameras (same person, same zone, same shift) and act, not just alert. |
| **Execution and demo** | It works live in the room on a real camera, with a recorded backup. 2 flawless violation types beat 3 shaky ones. |
| **User experience and design** | One glance: what happened, where, how bad, the clip. Calm, clear, built for a floor manager, not an ML engineer. |
| **Potential and impact** | Warehouse injuries are frequent and costly, and most sites already have cameras. Software only, works on existing CCTV. |

## How it fits together

```
camera / video file
      |
      v
backend/app/cv     pose estimation (lifting), zones, PPE, clip saving
      |  publish(event), set_frame(jpeg)        <- backend/app/bus.py, the only interface
      v
backend/app/main   FastAPI: /events/stream (SSE), /events/recent, /summary, /video (MJPEG), /clips
      |
      v
dashboard/         live feed, incident timeline, compliance score, heatmap, Claude "top risks"
```

`MODE=mock` (default) streams realistic fake incidents from `backend/app/mock.py`, so the dashboard never waits on the CV pipeline. `MODE=live` runs the real pipeline on a webcam or video file.

## The contract

Every incident is one JSON event, defined in `contract/event.schema.json` (TypeScript mirror `contract/event.ts`, Python `backend/app/schema.py`). Change all three together.

```json
{
  "id": "evt_20261031_113205_1_0007",
  "camera": "cam-1-loading-dock",
  "type": "unsafe_lift",
  "severity": "high",
  "timestamp": "2026-10-31T11:32:05Z",
  "zone": "loading-dock",
  "clip_url": "/clips/evt_20261031_113205_1_0007.mp4",
  "frame_url": "/clips/evt_20261031_113205_1_0007.jpg",
  "confidence": 0.91,
  "bbox": [412, 180, 590, 610],
  "detail": "back bent 62 degrees with straight legs while lifting"
}
```

Types: `unsafe_lift`, `no_hardhat`, `no_vest`, `restricted_zone`, `no_hairnet`, `no_gloves`.

## Who owns what

- **Layne (backend / CV):** pose estimation for the lifting violation first, then PPE and zone detection; the FastAPI stream of events and annotated frames; clip saving for the incident timeline.
- **Jas (frontend / product):** the dashboard (live feed, incident timeline, compliance score, heatmap); the weekly "top risks" summary (Claude on stored incidents); pitch, demo script and pre-recorded clips.

## Plan (11:00 to 16:00 submission)

Haunted Hacks: submissions close at **16:00** (Builderbase), judging 16:00 to 17:00, awards 17:00 to 18:00.

| Time | What |
| --- | --- |
| 11:00 to 11:30 | Event schema, repo setup, sample factory footage |
| 11:30 to 13:00 | Tune lifting detection on real footage; dashboard on mock data |
| 13:00 | Lunch |
| 13:30 to 14:45 | Swap mock for the real stream; track integration (Agent Relay, Durable Actors) |
| 14:45 to 15:30 | Polish, summary, full demo rehearsal, record the backup demo |
| 15:30 to 16:00 | Submit |

- **Cut line:** if detection is flaky by 14:00, ship what is solid and spend the rest on polish. A reliable demo of 2 beats a shaky demo of 3.
- **Backup:** keep a screen recording of a perfect run, so wifi or webcam trouble cannot sink the demo.
- **Tracks:** Agent Relay (camera agents and a safety supervisor talking directly) and Terse Durable Actors (live warehouse state for the dashboard); using both earns bonus points.
- **Stretch:** hygiene detection (hairnets and gloves), the differentiator against Intenseye and Voxel.

## Run it

```bash
# backend (mock events)
cd backend && uv run uvicorn app.main:app --reload --port 8000

# backend (real pipeline on webcam 0 or a video file)
cd backend && MODE=live SOURCE=0 uv run --extra live uvicorn app.main:app --port 8000

# dashboard
cd dashboard && npm install && npm run dev
```

See `backend/README.md`, `backend/app/cv/README.md` and `dashboard/README.md` for details.

## Team

- Layne Singh ([@laynebuilds](https://github.com/laynebuilds))
- Jasnoor Kaur ([@jasnoorkaur111](https://github.com/jasnoorkaur111))
