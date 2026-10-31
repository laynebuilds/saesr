# saesr

Computer vision threat detection for warehouses. Cameras you already have, watched by models that flag unsafe lifts, missing PPE and people in restricted zones the moment it happens, with a clip of every incident and a weekly summary of the top risks.

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

## Plan (11:00 to 18:00)

| Time | What |
| --- | --- |
| 11:00 to 11:30 | Event schema, repo setup, sample factory footage |
| 11:30 to 14:30 | Lifting detection end to end; dashboard on mock data |
| 14:30 to 16:00 | Swap mock for the real stream; second and third violation types |
| 16:00 to 17:00 | Polish, LLM summary, full demo rehearsal on the actual setup |
| 17:00 to 18:00 | Buffer, backup recorded demo, pitch practice |

- **Cut line:** if detection is flaky by 15:00, ship 2 violation types flawlessly and spend the time on polish. A reliable demo of 2 beats a shaky demo of 3.
- **Backup:** keep a screen recording of a perfect run, so wifi or webcam trouble cannot sink the demo.
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
