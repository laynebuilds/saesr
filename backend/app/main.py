"""Saesr API (contract v2). Ingests detections, serves live frames, and fronts the durable actors.

MODE=mock (default) feeds the bus from app.mock. MODE=live starts the CV pipeline from
app.cv.pipeline (imported lazily, so mock mode needs no CV dependencies). Every bus event is
forwarded to its CameraActor by app.actors; the relay service talks back through
POST /internal/cameras/{camera_id}/message. See backend/README.md for the routes table.
"""
import asyncio
import json
import logging
import os
import signal
import subprocess
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, TypeAdapter, ValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("saesr.api")


def _import_actors():
    """app.actors, or a disabled stand-in if it fails to import, so a broken actors module
    never keeps the API (events, frames, crash recovery) from starting."""
    try:
        from . import actors as mod
        return mod
    except Exception:
        log.exception("app.actors failed to import; running with actors DISABLED")
        import sys
        import types

        stub = types.ModuleType("app.actors")
        stub.enabled = lambda: False
        stub.start = lambda cameras: None
        stub.stop = lambda: None
        stub.status = lambda: {"enabled": False, "error": "app.actors failed to import"}
        stub.open_state_subscription = lambda: asyncio.Queue()
        stub.close_state_subscription = lambda q: None

        async def _unavailable(*args, **kwargs):
            return None

        stub.send = stub.camera_state = stub.site_state = stub.socket_grant = _unavailable
        sys.modules["app.actors"] = stub
        setattr(sys.modules[__package__], "actors", stub)
        return stub


actors = _import_actors()
from . import agents, bus, mock, relay_feed, supabase_sink  # noqa: E402
from .agents import activity  # noqa: E402
from .schema import CAMERAS, SITE_ID, VIOLATIONS, ActorMessage, Event, now_iso  # noqa: E402

MODE = os.environ.get("MODE", "mock").lower()
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CLIPS_DIR = BACKEND_DIR / "clips"
SAMPLES_DIR = REPO_DIR / "samples"
DATA_DIR = BACKEND_DIR / "data"
ACTORS_PID_FILE = Path(os.environ.get("ACTORS_PID_FILE", DATA_DIR / "actors.pid"))
KEEPALIVE_S = 15.0
MJPEG_FPS = 10
RESTART_BACKOFF_S = (1, 2, 5, 10, 30)  # producer restart delays; the last one repeats
CRASH_DELAY_S = 0.3  # time for the /admin/crash response to reach the caller
DEMO_CAMERAS = ["cam-01", "cam-02"]
CAMERA_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"

_actor_message = TypeAdapter(ActorMessage)


def _producer():
    """The coroutine factory for this mode: live CV pipeline, or mock events."""
    if MODE == "live":
        from .cv import pipeline  # lazy: CV deps only needed in live mode

        source = os.environ.get("SOURCE", "0")
        camera_id = os.environ.get("SAESR_CAMERA", "cam-01")
        return lambda: pipeline.run(bus.publish, bus.set_frame, source, camera_id=camera_id)

    async def publish_with_frame(event: Event):
        # Mock has no camera: show the sample still of the latest event as that camera's frame.
        still = SAMPLES_DIR / f"{mock.SAMPLE_FOR.get(event.type, 'unsafe_lift')}.jpg"
        if still.exists():
            bus.set_frame(still.read_bytes(), event.camera_id)
        await bus.publish(event)

    every = float(os.environ.get("MOCK_EVERY", "4"))
    return lambda: mock.run(publish_with_frame, every)


async def _supervise(make, name: str = "producer") -> None:
    """Keep a producer running: a crash is logged and restarted with backoff,
    so a bad frame or a camera hiccup never takes the API down."""
    attempt = 0
    while True:
        try:
            await make()
            log.warning("%s exited; restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed; restarting", name)
        await asyncio.sleep(RESTART_BACKOFF_S[min(attempt, len(RESTART_BACKOFF_S) - 1)])
        attempt += 1


def _start_actors() -> Optional[asyncio.Task]:
    cameras = list(DEMO_CAMERAS)
    if MODE == "live" and os.environ.get("SAESR_CAMERA") and os.environ["SAESR_CAMERA"] not in cameras:
        cameras.append(os.environ["SAESR_CAMERA"])
    try:
        return actors.start(cameras)
    except Exception:
        log.exception("actors bridge failed to start; continuing without it")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    loaded = bus.load_history()
    log.info("mode=%s, %d events loaded from %s", MODE, loaded, bus.EVENTS_LOG)
    task = None if MODE == "off" else asyncio.create_task(_supervise(_producer()))
    bridge = _start_actors()  # no-op unless the actors server is configured (see app/actors.py)
    sink = supabase_sink.start()  # no-op unless SUPABASE_URL and SUPABASE_SECRET_KEY are set
    agents_task = agents.start()  # old Agent Relay supervisor: off unless AGENTS_SUPERVISOR=1
    feed = relay_feed.start()  # relay agents' steps -> /agents/activity for the dashboard
    try:
        yield
    finally:
        for t in (task, bridge, sink, agents_task, feed, *[r["task"] for r in _replays.values()]):
            if t:
                t.cancel()
        _replays.clear()
        if agents_task:
            agents.stop()
        try:
            actors.stop()
        except Exception:
            log.exception("actors.stop failed")


app = FastAPI(title="Saesr API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=CLIPS_DIR), name="clips")
app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _require_actors() -> None:
    if not actors.enabled():
        raise HTTPException(503, "durable actors are off (set ACTORS_URL; see scripts/run_demo.sh)")


def _check_token(env: str, given: Optional[str]) -> None:
    expected = os.environ.get(env)
    if expected and given != expected:
        raise HTTPException(401, f"bad or missing token ({env} is set)")


# ---- health, events -------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"ok": True, "mode": MODE}


@app.post("/events")
async def post_event(event: Event):
    """One detection from the CV worker (or scripts/mock_events.py). Validated against schema.Event
    (422 on bad input). The actors bridge forwards it to the camera actor, which dedupes by event_id."""
    fresh = await bus.publish(event)
    return {"ok": True, "event_id": event.event_id, "duplicate": not fresh}


@app.get("/events/recent")
def events_recent(
    limit: int = Query(100, ge=1, le=bus.RECENT_MAX),
    hours: float | None = Query(None, gt=0),
    camera_id: str | None = Query(None, max_length=64),
):
    """Newest last. hours limits to the last N hours (e.g. hours=168 for a weekly summary)."""
    events = bus.recent(bus.RECENT_MAX if (hours or camera_id) else limit)
    if camera_id:
        events = [e for e in events if e.camera_id == camera_id]
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = [e for e in events if _parse_ts(e.timestamp) >= cutoff]
    return [e.model_dump(mode="json") for e in events[-limit:]]


@app.get("/events/stream")
async def events_stream(request: Request):
    """Server-Sent Events: `data: <Event json>` per new event."""
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


# ---- frames ---------------------------------------------------------------------------------


@app.get("/frame.jpg")
def frame(camera_id: str | None = Query(None, max_length=64)):
    jpeg = bus.latest_frame(camera_id)
    if jpeg is None:
        raise HTTPException(404, "no frame yet")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


BOUNDARY = "frame"


async def mjpeg(camera_id: Optional[str], is_disconnected):
    """Multipart JPEG chunks, only when that camera's frame changed."""
    sent = -1
    while not await is_disconnected():
        jpeg = bus.latest_frame(camera_id)
        seq = bus.frame_seq(camera_id)
        if jpeg is not None and seq != sent:
            sent = seq
            yield (
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n\r\n"
            ).encode() + jpeg + b"\r\n"
        await asyncio.sleep(1 / MJPEG_FPS)


@app.get("/video")
async def video(request: Request):
    """MJPEG of the most recent frame of any camera."""
    return StreamingResponse(mjpeg(None, request.is_disconnected), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}")


@app.get("/stream/{camera_id}")
async def stream_camera(request: Request, camera_id: str = PathParam(pattern=CAMERA_ID_PATTERN)):
    """MJPEG of one camera's annotated frames."""
    return StreamingResponse(mjpeg(camera_id, request.is_disconnected), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}")


# ---- summary --------------------------------------------------------------------------------

# Compliance score (local fallback; the actors own the real per-camera score).
# Each violation in the window carries a weight by type; compliant observations weigh 0.
# weighted_per_hour = sum(weights in window) / window_hours
# score = clamp(100 - weighted_per_hour * SCORE_K, 0, 100), with SCORE_K = 2.
# Window = the span from the oldest buffered event to now, at least 1 hour and at most `hours`.
TYPE_WEIGHT = {"unsafe_lift": 5, "zone_breach": 5, "missing_hairnet": 3, "missing_gloves": 3, "compliant_observation": 0}
SCORE_K = 2.0


@app.get("/summary")
def summary(hours: float = Query(24, gt=0, le=24 * 31)):
    """Stats over the last `hours` (default 24; 168 for the week). Violations only in by_* counts."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    window = [e for e in bus.recent(bus.RECENT_MAX) if _parse_ts(e.timestamp) >= cutoff]
    violations = [e for e in window if e.type in VIOLATIONS]
    if window:
        oldest = min(_parse_ts(e.timestamp) for e in window)
        span = max(1.0, (now - oldest).total_seconds() / 3600)
    else:
        span = 1.0
    per_hour = sum(TYPE_WEIGHT.get(e.type, 0) for e in violations) / span
    score = max(0.0, min(100.0, 100.0 - per_hour * SCORE_K))
    return {
        "total": len(violations),
        "compliant_observations": len(window) - len(violations),
        "window_hours": round(span, 2),
        "by_type": dict(Counter(e.type for e in violations)),
        "by_zone": dict(Counter(e.zone_id for e in violations)),
        "by_camera": dict(Counter(e.camera_id for e in violations)),
        "weighted_incidents_per_hour": round(per_hour, 2),
        "compliance_score": round(score, 1),
        "last_event": window[-1].model_dump(mode="json") if window else None,
        "mode": MODE,
    }


# ---- durable actors -------------------------------------------------------------------------


@app.post("/internal/cameras/{camera_id}/message")
async def camera_message(
    camera_id: str = PathParam(pattern=CAMERA_ID_PATTERN),
    body: Any = Body(...),
    x_internal_token: Optional[str] = Header(None),
):
    """The relay service (and the dashboard's approve button) talks to a CameraActor here.
    Body = one schema.ActorMessage (discriminated by "kind")."""
    _check_token("INTERNAL_TOKEN", x_internal_token)
    try:
        msg = _actor_message.validate_python(body)
    except ValidationError as err:
        raise HTTPException(422, json.loads(err.json()))
    _require_actors()
    result = await actors.send(camera_id, msg.model_dump(mode="json"))
    if not result.get("ok"):
        return JSONResponse(result, status_code=502)
    return result


@app.post("/api/cameras/{camera_id}/socket")
async def camera_socket(camera_id: str = PathParam(pattern=CAMERA_ID_PATTERN)):
    """Grant for a dashboard websocket on one CameraActor (open grant.websocketUrl)."""
    _require_actors()
    return await actors.socket_grant("camera", camera_id)


@app.post("/api/site/socket")
async def site_socket():
    """Grant for a dashboard websocket on the SiteActor."""
    _require_actors()
    return await actors.socket_grant("site", SITE_ID)


@app.get("/cameras/{camera_id}/state")
async def camera_state(camera_id: str = PathParam(pattern=CAMERA_ID_PATTERN)):
    _require_actors()
    state = await actors.camera_state(camera_id)
    if state is None:
        raise HTTPException(503, "camera actor unavailable")
    return state


@app.get("/site/state")
async def site_state():
    _require_actors()
    state = await actors.site_state()
    if state is None:
        raise HTTPException(503, "site actor unavailable")
    return state


@app.get("/actors/status")
def actors_status():
    try:
        return actors.status()
    except Exception as err:
        return {"enabled": actors.enabled(), "error": str(err)}


@app.get("/cameras")
def cameras():
    """Known demo cameras and whether they have a live frame."""
    live = set(bus.cameras_with_frames())
    return [{"camera_id": c, "description": d, "has_frame": c in live} for c, d in CAMERAS.items()]


# ---- simulated machine controller -----------------------------------------------------------

_machine_calls: deque = deque(maxlen=100)


@app.post("/mock/machine/slow")
async def mock_machine_slow(body: dict = Body(default_factory=dict)):
    """Stand-in for a real line controller: the fixer's tier-2 slow_machine action lands here
    once a human approved it. Nothing physical happens; the call is logged."""
    call = {"at": now_iso(), "request": body}
    _machine_calls.append(call)
    log.warning("SIMULATED machine slow-down: %s", json.dumps(body)[:300])
    return {"ok": True, "simulated": True, "machine_id": body.get("machine_id") or body.get("zone_id"), "at": call["at"]}


@app.get("/mock/machine/calls")
def mock_machine_calls():
    return list(_machine_calls)


# ---- admin: crash and replay ----------------------------------------------------------------


# SIGINT, not SIGKILL: after a SIGKILL each actor refuses calls for ~28 s until its old lock
# expires; after SIGINT the restarted server serves recovered state in ~2 s. CRASH_HARD=1 forces SIGKILL.
CRASH_SIGNAL = signal.SIGKILL if os.environ.get("CRASH_HARD") == "1" else signal.SIGINT


def _kill_tree(pid: int, sig: int = CRASH_SIGNAL) -> list[int]:
    """Kill a process and its descendants (uv run spawns a child python)."""
    killed: list[int] = []
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=2).stdout
        for child in out.split():
            killed += _kill_tree(int(child), sig)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        os.kill(pid, sig)
        killed.append(pid)
    except OSError:
        pass
    return killed


def _exit_process() -> None:
    log.warning("ADMIN CRASH: exiting the API process on purpose")
    os._exit(1)


@app.post("/admin/crash")
async def admin_crash(
    target: Literal["api", "actors", "all"] = Query("api"),
    x_admin_token: Optional[str] = Header(None),
):
    """Crash on purpose for the resilience demo. run_demo.sh restarts what died within ~1 to 2 s.
    target=api exits this process; actors kills the durable-actors dev server (via its pid file);
    all does both."""
    _check_token("ADMIN_TOKEN", x_admin_token)
    result: dict[str, Any] = {"ok": True, "target": target}
    if target in ("actors", "all"):
        try:
            pid = int(ACTORS_PID_FILE.read_text().strip())
        except (OSError, ValueError):
            if target == "actors":
                raise HTTPException(409, f"no actors pid file at {ACTORS_PID_FILE} (start with scripts/run_demo.sh)")
            pid = None
        result["actors_killed"] = _kill_tree(pid) if pid else []
    if target in ("api", "all"):
        asyncio.get_running_loop().call_later(CRASH_DELAY_S, _exit_process)
        result["api_exiting_in_s"] = CRASH_DELAY_S
    return result


class ReplayRequest(BaseModel):
    camera_id: str
    source: str
    loop: bool = True


_replays: dict[str, dict[str, Any]] = {}


def _load_pipeline():
    """app.cv.pipeline, or 503 if the live extra (cv2, ultralytics) is not installed."""
    try:
        import cv2  # noqa: F401
        import ultralytics  # noqa: F401
        from .cv import pipeline
    except ImportError as err:
        raise HTTPException(
            503, f"live CV extra not installed ({err.name or err}); run with `uv run --extra live ...`"
        )
    return pipeline


def _replay_view(camera_id: str, r: dict) -> dict:
    return {"camera_id": camera_id, "source": r["source"], "loop": r["loop"], "started_at": r["started_at"],
            "running": not r["task"].done()}


@app.post("/admin/replay")
async def admin_replay_start(req: ReplayRequest, x_admin_token: Optional[str] = Header(None)):
    """Run the CV pipeline on a video file (or camera index) for one camera, supervised
    (restarts on crash). Replaces any running replay for that camera."""
    _check_token("ADMIN_TOKEN", x_admin_token)
    pipeline = _load_pipeline()
    old = _replays.pop(req.camera_id, None)
    if old:
        old["task"].cancel()

    def make():
        return pipeline.run(bus.publish, bus.set_frame, req.source, camera_id=req.camera_id, loop=req.loop)

    task = asyncio.create_task(_supervise(make, name=f"replay {req.camera_id}"))
    _replays[req.camera_id] = {"task": task, "source": req.source, "loop": req.loop, "started_at": now_iso()}
    return {"ok": True, "replaced": old is not None, **_replay_view(req.camera_id, _replays[req.camera_id])}


@app.get("/admin/replay")
def admin_replay_list():
    return [_replay_view(c, r) for c, r in _replays.items()]


@app.delete("/admin/replay")
async def admin_replay_stop(camera_id: str | None = Query(None, max_length=64), x_admin_token: Optional[str] = Header(None)):
    """Stop the replay for camera_id, or all replays if omitted."""
    _check_token("ADMIN_TOKEN", x_admin_token)
    ids = [camera_id] if camera_id else list(_replays)
    stopped = []
    for c in ids:
        r = _replays.pop(c, None)
        if r:
            r["task"].cancel()
            stopped.append(c)
    return {"ok": True, "stopped": stopped}


# ---- old Agent Relay supervisor (off unless AGENTS_SUPERVISOR=1) ----------------------------


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
    """Acknowledge an incident (or "all") over Relay to the old supervisor."""
    if agents.current is None:
        raise HTTPException(503, "agents are off (AGENTS_SUPERVISOR=1 and Relay not configured)")
    try:
        msg = await agents.current.ack([event_id], by)
    except Exception as err:
        raise HTTPException(503, f"relay unavailable: {err}")
    if msg is None:
        raise HTTPException(503, "agents are not connected to Relay yet")
    return {"ok": True, "relay_message_id": msg.get("id")}
