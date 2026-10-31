"""Saesr API. Serves events, live frames and dashboard stats.

MODE=mock (default) feeds the bus from app.mock. MODE=live starts the CV pipeline
from app.cv.pipeline, imported lazily so mock mode needs no CV dependencies.
"""
import asyncio
import json
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import bus, mock

MODE = os.environ.get("MODE", "mock").lower()
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CLIPS_DIR = BACKEND_DIR / "clips"
SAMPLES_DIR = REPO_DIR / "samples"
KEEPALIVE_S = 15.0
MJPEG_FPS = 10


async def _start_producer() -> asyncio.Task:
    if MODE == "live":
        from .cv import pipeline  # lazy: CV deps only needed in live mode

        source = os.environ.get("SOURCE", "0")
        return asyncio.create_task(pipeline.run(bus.publish, bus.set_frame, source))

    async def publish_with_frame(event):
        # Mock has no camera: show the sample still of the latest event as the live frame.
        still = SAMPLES_DIR / f"{event.type}.jpg"
        if still.exists():
            bus.set_frame(still.read_bytes())
        await bus.publish(event)

    return asyncio.create_task(mock.run(publish_with_frame, float(os.environ.get("MOCK_EVERY", "4"))))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = await _start_producer()
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Saesr API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=CLIPS_DIR), name="clips")
app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")


@app.get("/health")
def health():
    return {"ok": True, "mode": MODE}


@app.get("/events/recent")
def events_recent(limit: int = Query(100, ge=1, le=500)):
    return [e.model_dump(mode="json") for e in bus.recent(limit)]


@app.get("/events/stream")
async def events_stream(request: Request):
    async def gen():
        it = bus.subscribe().__aiter__()
        nxt = asyncio.ensure_future(it.__anext__())
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                done, _ = await asyncio.wait({nxt}, timeout=KEEPALIVE_S)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                event = nxt.result()
                nxt = asyncio.ensure_future(it.__anext__())
                yield f"data: {event.model_dump_json()}\n\n"
        finally:
            nxt.cancel()
            await it.aclose()

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.get("/frame.jpg")
def frame():
    jpeg = bus.latest_frame()
    if jpeg is None:
        raise HTTPException(404, "no frame yet")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/video")
async def video(request: Request):
    boundary = "frame"

    async def gen():
        while not await request.is_disconnected():
            jpeg = bus.latest_frame()
            if jpeg is not None:
                yield (
                    f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                ).encode() + jpeg + b"\r\n"
            await asyncio.sleep(1 / MJPEG_FPS)

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}")


# Compliance score.
# Each incident in the window carries a severity weight: low=1, medium=3, high=5.
# weighted_per_hour = sum(weights in window) / window_hours
# score = clamp(100 - weighted_per_hour * SCORE_K, 0, 100), with SCORE_K = 2.
# So one high-severity incident per hour costs 10 points, ten per hour drives the
# score to 0. Window = the span from the oldest buffered event to now, at least
# 1 hour and at most 24 hours, so a fresh server is not punished by a tiny window.
SEVERITY_WEIGHT = {"low": 1, "medium": 3, "high": 5}
SCORE_K = 2.0


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@app.get("/summary")
def summary():
    now = datetime.now(timezone.utc)
    events = bus.recent(500)
    cutoff = now - timedelta(hours=24)
    window = [e for e in events if _parse_ts(e.timestamp) >= cutoff]
    if window:
        oldest = min(_parse_ts(e.timestamp) for e in window)
        hours = max(1.0, (now - oldest).total_seconds() / 3600)
    else:
        hours = 1.0
    weighted = sum(SEVERITY_WEIGHT[e.severity] for e in window)
    per_hour = weighted / hours
    score = max(0.0, min(100.0, 100.0 - per_hour * SCORE_K))
    return {
        "total": len(window),
        "window_hours": round(hours, 2),
        "by_type": dict(Counter(e.type for e in window)),
        "by_zone": dict(Counter(e.zone for e in window)),
        "by_camera": dict(Counter(e.camera for e in window)),
        "by_severity": dict(Counter(e.severity for e in window)),
        "weighted_incidents_per_hour": round(per_hour, 2),
        "compliance_score": round(score, 1),
        "last_event": window[-1].model_dump(mode="json") if window else None,
        "mode": MODE,
    }
