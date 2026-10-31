"""Bridge between the backend and the durable actors (durable/src/actors.py), contract v2.

What runs after start(cameras):
  pump + send loop   every bus event -> Camera(event.camera_id).message({"kind": "detection", ...}),
                     in order, through a bounded buffer (oldest dropped when full) with retry + backoff.
  heartbeat loop     Camera(cam).message({"kind": "heartbeat"}) every ACTORS_HEARTBEAT_S per camera.
  relay dispatcher   reads each camera's persisted outbox (Camera.pending_relay) and POSTs every
                     pending item as schema.RelayStart JSON to {RELAY_URL}/relay/start with header
                     X-Idempotency-Key = outbox id. Only after a 2xx does it call
                     Camera.relay_delivered(id). Anything not acknowledged stays in the actor and is
                     resent on the next pass, including after a backend or actors-server restart.
  state watchers     one socket per camera; every CameraState broadcast goes to state subscribers
                     (open_state_subscription), as do the states returned by send().

Enabled only when ACTORS_URL (or DURABLE_ACTORS_CONTROL_PLANE_URL) is set. Nothing here raises
into the API: every public call returns a value or None and is bounded by a timeout.
"""
import asyncio
import logging
import os
from collections import deque
from typing import Any, Iterable, Literal, Optional, Protocol

from pydantic import TypeAdapter, ValidationError

from . import bus
from .schema import CAMERAS, SITE_ID, ActorMessage, Event

log = logging.getLogger("saesr.actors")

ACTORS_URL = os.environ.get("ACTORS_URL") or os.environ.get("DURABLE_ACTORS_CONTROL_PLANE_URL")
RELAY_URL = (os.environ.get("RELAY_URL") or "").rstrip("/") or None
BUFFER_MAX = int(os.environ.get("ACTORS_BUFFER_MAX", "1000"))
HEARTBEAT_S = float(os.environ.get("ACTORS_HEARTBEAT_S", "5"))
DISPATCH_S = float(os.environ.get("RELAY_DISPATCH_S", "1"))
STATE_QUEUE_MAX = 256
BACKOFF_S = (0.5, 1, 2, 5, 10)  # retry delays; the last one repeats
MAX_PERMANENT_FAILURES = 5  # an event the actor keeps rejecting is dropped after this many tries
CALL_TIMEOUT_S = 10.0
RELAY_TIMEOUT_S = 5.0

TRANSIENT_CODES = {"unavailable", "outcome_unknown", "deadline_exceeded", "resource_exhausted", "aborted"}
_MESSAGE = TypeAdapter(ActorMessage)


def enabled() -> bool:
    return bool(ACTORS_URL)


class ActorsClient(Protocol):
    """The subset of durable_actors.AsyncClient the bridge uses (faked in tests)."""

    async def invoke(self, actor_name: str, actor_id: str, method: str, args: list[Any]) -> Any: ...
    async def prepare_websocket(self, actor_name: str, actor_id: str, metadata: Any) -> Any: ...
    async def aclose(self) -> None: ...


class RelayPoster(Protocol):
    async def __call__(self, url: str, body: dict, idempotency_key: str) -> int: ...


def make_client(url: str) -> ActorsClient:
    from durable_actors import AsyncClient  # lazy: only needed when the bridge is on

    return AsyncClient(control_plane_url=url)


async def http_post(url: str, body: dict, idempotency_key: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=RELAY_TIMEOUT_S) as http:
        r = await http.post(url, json=body, headers={"X-Idempotency-Key": idempotency_key})
        return r.status_code


def is_transient(err: BaseException) -> bool:
    """Network trouble or a runtime that did not run the call: retry forever. Anything else
    (the actor raised, bad arguments) counts toward MAX_PERMANENT_FAILURES."""
    code = getattr(err, "code", None)
    if isinstance(code, str):
        return code in TRANSIENT_CODES
    return True


# State fan-out ------------------------------------------------------------------

_state_subs: set[asyncio.Queue] = set()
_last_version: dict[str, int] = {}


def open_state_subscription() -> asyncio.Queue:
    """A bounded queue (drop-oldest) that receives a CameraState dict after every actor mutation
    the backend observes. Always pair with close_state_subscription."""
    q: asyncio.Queue = asyncio.Queue(maxsize=STATE_QUEUE_MAX)
    _state_subs.add(q)
    return q


def close_state_subscription(q: asyncio.Queue) -> None:
    _state_subs.discard(q)


def publish_state(state: Any) -> None:
    """Fan a CameraState dict out to subscribers, once per (camera, version)."""
    if not isinstance(state, dict) or "camera_id" not in state:
        return
    cam, version = state["camera_id"], int(state.get("version", 0))
    if version <= _last_version.get(cam, -1):
        return
    _last_version[cam] = version
    for q in list(_state_subs):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(state)


# Bridge -------------------------------------------------------------------------


class Bridge:
    def __init__(
        self,
        client: ActorsClient,
        cameras: Iterable[str] = (),
        relay_url: Optional[str] = RELAY_URL,
        post: RelayPoster = http_post,
        buffer_max: int = BUFFER_MAX,
        backoff: tuple[float, ...] = BACKOFF_S,
        heartbeat_s: float = HEARTBEAT_S,
        dispatch_s: float = DISPATCH_S,
        watch: bool = True,
    ):
        self.client = client
        self.relay_url = relay_url
        self.post = post
        self.backoff = backoff
        self.heartbeat_s = heartbeat_s
        self.dispatch_s = dispatch_s
        self.watch = watch
        self.buffer: deque[Event] = deque(maxlen=buffer_max)
        self.cameras: list[str] = list(dict.fromkeys(cameras))
        self.wake = asyncio.Event()
        self.dispatch_wake = asyncio.Event()
        self.stats = {
            "sent": 0, "retries": 0, "dropped_overflow": 0, "dropped_rejected": 0, "heartbeats": 0,
            "relay_posted": 0, "relay_failed": 0, "relay_pending": 0,
        }
        self.connected = False
        self.last_error: Optional[str] = None
        self.relay_warned = False
        self.watchers_connected: set[str] = set()

    # Actor calls -------------------------------------------------------------

    async def message(self, camera_id: str, msg: dict) -> dict:
        """Camera.message; publishes the resulting state and wakes the dispatcher. Raises on failure."""
        result = await asyncio.wait_for(
            self.client.invoke("Camera", camera_id, "message", [msg]), CALL_TIMEOUT_S
        )
        self._mark_up()
        if isinstance(result, dict):
            publish_state(result.get("state"))
            if result.get("outbox"):
                self.dispatch_wake.set()
        return result

    def add_camera(self, camera_id: str) -> None:
        if camera_id not in self.cameras:
            self.cameras.append(camera_id)

    # Detections --------------------------------------------------------------

    def enqueue(self, event: Event) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.stats["dropped_overflow"] += 1  # deque drops the oldest on append
        self.buffer.append(event)
        self.add_camera(event.camera_id)
        self.wake.set()

    async def pump(self) -> None:
        """Move every bus event into the buffer. Never blocks on the actors server."""
        q = bus.open_subscription()
        try:
            while True:
                self.enqueue(await q.get())
        finally:
            bus.close_subscription(q)

    async def send_loop(self) -> None:
        """Deliver buffered events in order. The head event stays until delivered (or rejected
        MAX_PERMANENT_FAILURES times). Retries are safe: the Camera dedupes by event_id."""
        attempt = rejected = 0
        while True:
            if not self.buffer:
                self.wake.clear()
                await self.wake.wait()
                continue
            event = self.buffer[0]
            try:
                await self.message(event.camera_id, {"kind": "detection", "event": event.model_dump(mode="json")})
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._mark_down(err)
                if not is_transient(err):
                    rejected += 1
                    if rejected >= MAX_PERMANENT_FAILURES:
                        log.error("dropping event %s after %d rejections: %s", event.event_id, rejected, err)
                        self._pop(event)
                        self.stats["dropped_rejected"] += 1
                        rejected = attempt = 0
                        continue
                self.stats["retries"] += 1
                await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                attempt += 1
                continue
            self._pop(event)
            self.stats["sent"] += 1
            attempt = rejected = 0

    def _pop(self, event: Event) -> None:
        if self.buffer and self.buffer[0] is event:
            self.buffer.popleft()

    # Heartbeats ----------------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        while True:
            for camera in list(self.cameras):
                try:
                    await self.message(camera, {"kind": "heartbeat"})
                    self.stats["heartbeats"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self._mark_down(err)
                    break  # server is down; try again next tick
            await asyncio.sleep(self.heartbeat_s)

    # Relay dispatcher --------------------------------------------------------------

    async def dispatch_once(self) -> int:
        """One pass over every camera's outbox. Returns how many items were delivered."""
        delivered = pending = 0
        for camera in list(self.cameras):
            try:
                items = await asyncio.wait_for(
                    self.client.invoke("Camera", camera, "pending_relay", []), CALL_TIMEOUT_S
                )
                self._mark_up()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._mark_down(err)
                return delivered
            pending += len(items or [])
            if not items:
                continue
            if not self.relay_url:
                if not self.relay_warned:
                    log.warning("RELAY_URL not set: %d relay call(s) stay pending in the actors", len(items))
                    self.relay_warned = True
                continue
            for item in items:
                try:
                    status = await self.post(f"{self.relay_url}/relay/start", item["start"], item["id"])
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    status = 0
                    log.warning("relay POST %s failed: %s", item["id"], err)
                if not 200 <= status < 300:
                    self.stats["relay_failed"] += 1
                    break  # keep order per camera; retry this item next pass
                self.stats["relay_posted"] += 1
                try:
                    await asyncio.wait_for(
                        self.client.invoke("Camera", camera, "relay_delivered", [item["id"]]), CALL_TIMEOUT_S
                    )
                    delivered += 1
                    pending -= 1
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    # The relay got it but the actor did not hear back: it stays pending and is
                    # resent; the relay dedupes on X-Idempotency-Key.
                    self._mark_down(err)
                    return delivered
        self.stats["relay_pending"] = pending
        return delivered

    async def dispatch_loop(self) -> None:
        attempt = 0
        while True:
            self.dispatch_wake.clear()
            before = self.stats["relay_failed"]
            await self.dispatch_once()
            failed = self.stats["relay_failed"] > before
            delay = self.backoff[min(attempt, len(self.backoff) - 1)] if failed else self.dispatch_s
            attempt = attempt + 1 if failed else 0
            try:
                await asyncio.wait_for(self.dispatch_wake.wait(), timeout=max(delay, self.dispatch_s))
            except asyncio.TimeoutError:
                pass

    # State watchers ---------------------------------------------------------------

    async def watch_camera(self, camera: str) -> None:
        """Keep a socket open to Camera(camera); publish every CameraState it broadcasts."""
        from durable_actors.async_connection import AsyncConnection
        from durable_actors.json import JsonValue

        adapter = TypeAdapter(JsonValue)
        attempt = 0
        while True:
            try:
                grant = await asyncio.wait_for(
                    self.client.prepare_websocket("Camera", camera, {"name": "backend"}), CALL_TIMEOUT_S
                )
                conn = await AsyncConnection.open(grant, adapter, adapter, adapter, adapter)
                _last_version.pop(camera, None)  # accept whatever the actor has now
                self.watchers_connected.add(camera)
                attempt = 0
                try:
                    async for msg in conn:
                        publish_state(msg)
                finally:
                    self.watchers_connected.discard(camera)
                    try:
                        await conn.aclose()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.debug("camera watcher %s: %s", camera, err)
            await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
            attempt += 1

    # Status ---------------------------------------------------------------------

    def _mark_down(self, err: BaseException) -> None:
        first = self.last_error is None or self.connected
        self.last_error = f"{type(err).__name__}: {err}"
        if first:
            log.warning("actors server unreachable (%s); buffering %d events", self.last_error, len(self.buffer))
        self.connected = False

    def _mark_up(self) -> None:
        if not self.connected:
            log.info("actors server connected; %d events buffered", len(self.buffer))
        self.connected = True

    def status(self) -> dict:
        return {
            "enabled": True,
            "connected": self.connected,
            "actors_url": ACTORS_URL,
            "relay_url": self.relay_url,
            "site": SITE_ID,
            "cameras": list(self.cameras),
            "watchers": sorted(self.watchers_connected),
            "buffered": len(self.buffer),
            "last_error": self.last_error,
            **self.stats,
        }

    async def run(self) -> None:
        coros = [self.pump(), self.send_loop(), self.heartbeat_loop(), self.dispatch_loop()]
        if self.watch:
            coros += [self.watch_camera(c) for c in self.cameras]
        tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.client.aclose()
            except Exception:
                pass


# Module interface (used by main.py routes) ------------------------------------------

_bridge: Optional[Bridge] = None
_task: Optional[asyncio.Task] = None


def start(cameras: list[str]) -> Optional[asyncio.Task]:
    """Start the bridge if ACTORS_URL is set. Returns the task to cancel on shutdown, or None.
    A missing SDK or a bad config is logged, never raised."""
    global _bridge, _task
    if not enabled():
        return None
    try:
        client = make_client(ACTORS_URL)  # type: ignore[arg-type]
    except Exception:
        log.exception("actors bridge disabled: could not create the client (uv run --extra actors?)")
        return None
    _bridge = Bridge(client, cameras=cameras or list(CAMERAS))
    log.info("actors bridge on: %s site=%s relay=%s", ACTORS_URL, SITE_ID, RELAY_URL or "unset")
    _task = asyncio.create_task(_bridge.run())
    return _task


def stop() -> None:
    global _bridge, _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None
    _bridge = None


def current() -> Optional[Bridge]:
    return _bridge


async def send(camera_id: str, msg: dict) -> dict:
    """Apply one ActorMessage to Camera(camera_id). Returns {"ok": True, "changed", "version",
    "state"} or {"ok": False, "error"}. Bounded by CALL_TIMEOUT_S; never raises."""
    try:
        parsed = _MESSAGE.validate_python(msg)
    except ValidationError as err:
        return {"ok": False, "error": f"invalid message: {err.errors(include_url=False)}"}
    if _bridge is None:
        return {"ok": False, "error": "actors disabled"}
    if camera_id not in CAMERAS and camera_id not in _bridge.cameras:
        return {"ok": False, "error": f"unknown camera {camera_id}"}
    try:
        result = await _bridge.message(camera_id, parsed.model_dump(mode="json"))
    except asyncio.TimeoutError:
        return {"ok": False, "error": "actors timeout"}
    except Exception as err:
        _bridge._mark_down(err)
        return {"ok": False, "error": f"{type(err).__name__}: {err}"}
    _bridge.add_camera(camera_id)
    return {"ok": True, "changed": result.get("changed"), "version": result.get("version"), "state": result.get("state")}


async def _read(actor: str, actor_id: str, method: str) -> Optional[dict]:
    if _bridge is None:
        return None
    try:
        out = await asyncio.wait_for(_bridge.client.invoke(actor, actor_id, method, []), 3.0)
    except Exception as err:
        log.warning("%s(%s).%s unavailable: %s", actor, actor_id, method, err)
        return None
    return out if isinstance(out, dict) else None


async def camera_state(camera_id: str) -> Optional[dict]:
    """CameraState dict, or None if the bridge is off or the server is unreachable."""
    state = await _read("Camera", camera_id, "state")
    publish_state(state)
    return state


async def site_state() -> Optional[dict]:
    """SiteState dict, or None if the bridge is off or the server is unreachable."""
    return await _read("Site", SITE_ID, "state")


async def socket_grant(kind: Literal["camera", "site"], actor_id: str) -> dict:
    """prepare_websocket grant for a dashboard socket: {"websocketUrl", "homeRegion", "connectByMs"}.
    On failure returns {"error": "..."} (no websocketUrl); never raises."""
    if _bridge is None:
        return {"error": "actors disabled"}
    name = {"camera": "Camera", "site": "Site"}.get(kind)
    if name is None:
        return {"error": f"unknown kind {kind}"}
    try:
        grant = await asyncio.wait_for(
            _bridge.client.prepare_websocket(name, actor_id, {"name": "dashboard"}), CALL_TIMEOUT_S
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    if hasattr(grant, "model_dump"):
        return grant.model_dump(mode="json", by_alias=True)
    return dict(grant)


def status() -> dict:
    """Bridge health for GET /actors/status."""
    if _bridge is None:
        return {"enabled": enabled(), "running": False}
    return {**_bridge.status(), "running": _task is not None and not _task.done()}


# Kept for older callers in main.py.
site_snapshot = site_state
