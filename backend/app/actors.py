"""Bridge from the event bus to the durable actors (durable/src/actors.py).

Every event published on the bus is forwarded as Camera(event.camera).record(event); the
Camera actor forwards it to its Site actor, which owns incidents, counters, score and open
alerts and broadcasts a snapshot to every dashboard socket. A heartbeat task reports each
camera's frame rate so Camera actors can mark themselves offline when frames stop.

Enabled only when ACTORS_URL (or DURABLE_ACTORS_CONTROL_PLANE_URL) is set. The API never
depends on the actors server being up: events wait in a bounded buffer (oldest dropped when
full), sends retry with backoff, and nothing here raises into the API.

Delivery is at-least-once: a retry after a lost response ("outcome_unknown") may resend an
event that already landed. That is safe because Site.record is idempotent by event id.
"""
import asyncio
import logging
import os
import time
from collections import deque
from typing import Any, Iterable, Optional, Protocol

from . import bus
from .schema import SaesrEvent

log = logging.getLogger("saesr.actors")

ACTORS_URL = os.environ.get("ACTORS_URL") or os.environ.get("DURABLE_ACTORS_CONTROL_PLANE_URL")
SITE_ID = os.environ.get("SITE_ID", "demo-warehouse")
BUFFER_MAX = int(os.environ.get("ACTORS_BUFFER_MAX", "1000"))
HEARTBEAT_S = float(os.environ.get("ACTORS_HEARTBEAT_S", "5"))
BACKOFF_S = (0.5, 1, 2, 5, 10)  # retry delays; the last one repeats
MAX_PERMANENT_FAILURES = 5  # an event the actor keeps rejecting is dropped after this many tries
CALL_TIMEOUT_S = 10.0
FRAMES_STALE_S = 30.0  # no new frame for this long = do not heartbeat (let the Camera go offline)

# Codes where the call may simply not have run yet; retry without limit (the buffer bounds memory).
TRANSIENT_CODES = {"unavailable", "outcome_unknown", "deadline_exceeded", "resource_exhausted", "aborted"}


def enabled() -> bool:
    return bool(ACTORS_URL)


class ActorsClient(Protocol):
    async def record(self, camera: str, event: dict, site: str) -> Any: ...
    async def heartbeat(self, camera: str, fps: Optional[float], site: str) -> Any: ...
    async def snapshot(self, site: str) -> dict: ...
    async def aclose(self) -> None: ...


class SdkClient:
    """ActorsClient backed by the generated async client (app/actors_generated)."""

    def __init__(self, url: str):
        from durable_actors import AsyncClient  # lazy: only needed when the bridge is on

        from .actors_generated import async_actors

        self._actors = async_actors
        self._transport = AsyncClient(control_plane_url=url)

    async def record(self, camera: str, event: dict, site: str) -> Any:
        a = self._actors.Camera
        return await a.get(camera, self._transport).record(a.SaesrEvent.model_validate(event), site)

    async def heartbeat(self, camera: str, fps: Optional[float], site: str) -> Any:
        return await self._actors.Camera.get(camera, self._transport).heartbeat(fps, site)

    async def snapshot(self, site: str) -> dict:
        snap = await self._actors.Site.get(site, self._transport).snapshot()
        return snap.model_dump(mode="json")

    async def aclose(self) -> None:
        await self._transport.aclose()


def is_transient(err: BaseException) -> bool:
    """Network trouble or a runtime that did not run the call: retry forever.
    Anything else (the actor raised, bad arguments) counts toward MAX_PERMANENT_FAILURES."""
    code = getattr(err, "code", None)
    if isinstance(code, str):
        return code in TRANSIENT_CODES
    return True  # connection refused, DNS, timeouts, HTTP errors: the server is down or restarting


class Bridge:
    def __init__(
        self,
        client: ActorsClient,
        site: str = SITE_ID,
        buffer_max: int = BUFFER_MAX,
        backoff: tuple[float, ...] = BACKOFF_S,
        heartbeat_s: float = HEARTBEAT_S,
        cameras: Iterable[str] = (),
    ):
        self.client = client
        self.site = site
        self.backoff = backoff
        self.heartbeat_s = heartbeat_s
        self.buffer: deque[SaesrEvent] = deque(maxlen=buffer_max)
        self.cameras: set[str] = set(cameras)
        self.wake = asyncio.Event()
        self.stats = {"sent": 0, "retries": 0, "dropped_overflow": 0, "dropped_rejected": 0, "heartbeats": 0}
        self.connected = False
        self.last_error: Optional[str] = None

    # Intake -------------------------------------------------------------

    def enqueue(self, event: SaesrEvent) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.stats["dropped_overflow"] += 1  # deque drops the oldest on append
        self.buffer.append(event)
        self.cameras.add(event.camera)
        self.wake.set()

    async def pump(self) -> None:
        """Move every bus event into the buffer. Never blocks on the actors server."""
        q = bus.open_subscription()
        try:
            while True:
                self.enqueue(await q.get())
        finally:
            bus.close_subscription(q)

    # Delivery -----------------------------------------------------------

    async def send_loop(self) -> None:
        """Deliver buffered events in order. The head event stays put until it is delivered
        (or rejected MAX_PERMANENT_FAILURES times), so a server outage loses nothing that fits
        in the buffer."""
        attempt = 0
        rejected = 0
        while True:
            if not self.buffer:
                self.wake.clear()
                await self.wake.wait()
                continue
            event = self.buffer[0]
            try:
                await asyncio.wait_for(
                    self.client.record(event.camera, event.model_dump(mode="json"), self.site), CALL_TIMEOUT_S
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._mark_down(err)
                if not is_transient(err):
                    rejected += 1
                    if rejected >= MAX_PERMANENT_FAILURES:
                        log.error("dropping event %s after %d rejections: %s", event.id, rejected, err)
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
            self._mark_up()

    def _pop(self, event: SaesrEvent) -> None:
        # The head can only have changed if the buffer overflowed while we were sending.
        if self.buffer and self.buffer[0] is event:
            self.buffer.popleft()

    # Heartbeat ----------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        """Report fps per known camera while frames are flowing. When frames stop, heartbeats
        stop and each Camera actor's alarm marks it offline on its own."""
        last_seq, last_t, last_new_frame = bus.frame_seq(), time.monotonic(), time.monotonic()
        while True:
            await asyncio.sleep(self.heartbeat_s)
            seq, t = bus.frame_seq(), time.monotonic()
            if seq != last_seq:
                last_new_frame = t
            fps = round((seq - last_seq) / max(t - last_t, 1e-6), 2)
            last_seq, last_t = seq, t
            if t - last_new_frame > FRAMES_STALE_S:
                continue
            for camera in sorted(self.cameras):
                try:
                    await asyncio.wait_for(self.client.heartbeat(camera, fps, self.site), CALL_TIMEOUT_S)
                    self.stats["heartbeats"] += 1
                    self._mark_up()
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self._mark_down(err)
                    break  # server is down; try again next tick

    # Status -------------------------------------------------------------

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
            "site": self.site,
            "buffered": len(self.buffer),
            "last_error": self.last_error,
            **self.stats,
        }

    async def run(self) -> None:
        tasks = [asyncio.create_task(c) for c in (self.pump(), self.send_loop(), self.heartbeat_loop())]
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


_bridge: Optional[Bridge] = None


def start(cameras: Iterable[str] = ()) -> Optional[asyncio.Task]:
    """Start the bridge if ACTORS_URL is set. Returns the task to cancel on shutdown, or None.
    A missing SDK or a bad config is logged, never raised: the API keeps working without actors."""
    global _bridge
    if not enabled():
        return None
    try:
        client = SdkClient(ACTORS_URL)
    except Exception:
        log.exception("actors bridge disabled: could not create the client (pip install durable-actors?)")
        return None
    _bridge = Bridge(client, cameras=cameras)
    log.info("actors bridge on: %s site=%s", ACTORS_URL, SITE_ID)
    return asyncio.create_task(_bridge.run())


def current() -> Optional[Bridge]:
    return _bridge


async def site_snapshot() -> Optional[dict]:
    """The Site actor's snapshot, or None if the bridge is off or the server is unreachable."""
    if _bridge is None:
        return None
    try:
        return await asyncio.wait_for(_bridge.client.snapshot(_bridge.site), 3.0)
    except Exception as err:
        log.warning("site snapshot unavailable: %s", err)
        return None
