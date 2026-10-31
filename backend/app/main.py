"""Saesr API. Serves events, live frames and dashboard stats.

MODE=mock (default) feeds the bus from app.mock. MODE=live starts the CV pipeline
from app.cv.pipeline, imported lazily so mock mode needs no CV dependencies.
"""
import asyncio
import json
import logging
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agents, bus, mock
from .agents import activity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("saesr.api")

MODE = os.environ.get("MODE", "mock").lower()
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CLIPS_DIR = BACKEND_DIR / "clips"
SAMPLES_DIR = REPO_DIR / "samples"
KEEPALIVE_S = 15.0
MJPEG_FPS = 10
RESTART_BACKOFF_S = (1, 2, 5, 10, 30)  # producer restart delays; the last one repeats


def _producer():
    """The coroutine factory for this mode: live CV pipeline, or mock events."""
    if MODE == "live":
        from .cv import pipeline  # lazy: CV deps only needed in live mode

        source = os.environ.get("SOURCE", "0")
        return lambda: pipeline.run(bus.publish, bus.set_frame, source)

    async def publish_with_frame(event):
        # Mock has no camera: show the sample still of the latest event as the live frame.
        still = SAMPLES_DIR / f"{event.type}.jpg"
        if still.exists():
            bus.set_frame(still.read_bytes())
        await bus.publish(event)

    every = float(os.environ.get("MOCK_EVERY", "4"))
    return lambda: mock.run(publish_with_frame, every)


async def _supervise(make) -> None:
    """Keep the producer running: a crash in the pipeline is logged and restarted with backoff,
    so a bad frame or a camera hiccup never takes the API down."""
    attempt = 0
    while True:
        try:
            await make()
            log.warning("producer exited; restarting")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("producer crashed; restarting")
        await asyncio.sleep(RESTART_BACKOFF_S[min(attempt, len(RESTART_BACKOFF_S) - 1)])
        attempt += 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    loaded = bus.load_history()
    log.info("mode=%s, %d events loaded from %s", MODE, loaded, bus.EVENTS_LOG)
    task = asyncio.create_task(_supervise(_producer()))
    agents_task = agents.start()  # None unless Relay is configured; never blocks or crashes startup
    try:
        yield
    finally:
        task.cancel()
        if agents_task:
            agents_task.cancel()
            agents.stop()


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
def events_recent(limit: int = Query(100, ge=1, le=bus.RECENT_MAX), hours: float | None = Query(None, gt=0)):
    """Newest last. hours limits to the last N hours (e.g. hours=168 for a weekly summary)."""
    events = bus.recent(bus.RECENT_MAX if hours else limit)
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = [e for e in events if _parse_ts(e.timestamp) >= cutoff][-limit:]
    return [e.model_dump(mode="json") for e in events]


@app.get("/events/stream")
async def events_stream(request: Request):
    async def gen():
        q = bus.open_subscription()
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {event.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            pass  # client went away; nothing to clean beyond the subscription
        finally:
            bus.close_subscription(q)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.get("/agents/activity")
def agents_activity(limit: int = Query(100, ge=1, le=activity.RECENT_MAX), kind: str | None = Query(None, pattern="^(message|action)$")):
    """Recent agent messages and supervisor actions, newest last, plus the agent layer status."""
    return {"status": activity.status, "items": activity.recent(limit, kind)}


@app.get("/agents/stream")
async def agents_stream(request: Request):
    """Server-Sent Events: `data: <activity item json>` per agent message or action."""
    async def gen():
        q = activity.open_subscription()
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(q.get(), KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            activity.close_subscription(q)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.post("/agents/ack/{event_id}")
async def agents_ack(
    event_id: str = PathParam(pattern=r"^(all|evt_[A-Za-z0-9_\-]+)$", max_length=128),
    by: str = Query("dashboard", pattern=r"^[A-Za-z0-9 _.@\-]+$", max_length=64),
):
    """Acknowledge an incident (or "all") from the dashboard. Goes over Relay to the supervisor."""
    if agents.current is None:
        raise HTTPException(503, "agents are off (Relay not configured)")
    try:
        msg = await agents.current.ack([event_id], by)
    except Exception as err:
        raise HTTPException(503, f"relay unavailable: {err}")
    if msg is None:
        raise HTTPException(503, "agents are not connected to Relay yet")
    return {"ok": True, "relay_message_id": msg.get("id")}


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
        sent = -1
        while not await request.is_disconnected():
            jpeg = bus.latest_frame()
            if jpeg is not None and bus.frame_seq() != sent:
                sent = bus.frame_seq()
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
# 1 hour and at most `hours` (default 24), so a fresh server is not punished by a tiny window.
SEVERITY_WEIGHT = {"low": 1, "medium": 3, "high": 5}
SCORE_K = 2.0


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@app.get("/summary")
def summary(hours: float = Query(24, gt=0, le=24 * 31)):
    """Stats over the last `hours` (default 24; 168 for the week)."""
    now = datetime.now(timezone.utc)
    events = bus.recent(bus.RECENT_MAX)
    cutoff = now - timedelta(hours=hours)
    window = [e for e in events if _parse_ts(e.timestamp) >= cutoff]
    if window:
        oldest = min(_parse_ts(e.timestamp) for e in window)
        span = max(1.0, (now - oldest).total_seconds() / 3600)
    else:
        span = 1.0
    weighted = sum(SEVERITY_WEIGHT[e.severity] for e in window)
    per_hour = weighted / span
    score = max(0.0, min(100.0, 100.0 - per_hour * SCORE_K))
    return {
        "total": len(window),
        "window_hours": round(span, 2),
        "by_type": dict(Counter(e.type for e in window)),
        "by_zone": dict(Counter(e.zone for e in window)),
        "by_camera": dict(Counter(e.camera for e in window)),
        "by_severity": dict(Counter(e.severity for e in window)),
        "weighted_incidents_per_hour": round(per_hour, 2),
        "compliance_score": round(score, 1),
        "last_event": window[-1].model_dump(mode="json") if window else None,
        "mode": MODE,
    }
