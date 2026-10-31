# saesr durable actors

The warehouse state behind saesr lives in [Durable Actors](https://github.com/TerseAI/durable-actors):
one `Site` actor per warehouse and one `Camera` actor per camera feed, written in Python
(`src/actors.py`). The FastAPI backend forwards every detected incident to them through a
bridge (`backend/app/actors.py`), and dashboards subscribe to the `Site` over a WebSocket to get
a fresh snapshot on every change.

## Run it

Requires Python 3.11+ and uv. No Bun needed: the Python CLI ships the native runtime.

```sh
# terminal 1: actors server (state is kept in durable/.durable-actors/)
cd durable
uv run durable-actors dev                      # http://127.0.0.1:7100

# terminal 2: backend in mock mode with the bridge on
cd backend
ACTORS_URL=http://127.0.0.1:7100 MODE=mock uv run --extra actors uvicorn app.main:app --port 8000

# terminal 3: watch the live snapshots a dashboard would get
cd durable
PYTHONPATH=. uv run python scripts/watch_site.py demo-warehouse

# inspect / observe
uv run durable-actors inspect Site/demo-warehouse
uv run durable-actors observe
```

Without `ACTORS_URL` the bridge is off and the backend behaves exactly as before.

Backend env:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ACTORS_URL` (or `DURABLE_ACTORS_CONTROL_PLANE_URL`) | unset = bridge off | actors server |
| `SITE_ID` | `demo-warehouse` | Site actor id this backend reports to |
| `ACTORS_BUFFER_MAX` | `1000` | events held while the actors server is down (oldest dropped beyond this) |
| `ACTORS_HEARTBEAT_S` | `5` | camera heartbeat interval |

Backend endpoints added: `GET /site` (Site snapshot; `source` is `actors`, or `local` when it
falls back to computing from the bus) and `GET /actors/status` (bridge health: connected,
buffered, sent, retries, drops).

After changing an actor method or model, regenerate and vendor the client:
`./scripts/sync_client.sh` (writes `durable/generated/` and copies it to `backend/app/actors_generated/`).

## The actor model

```
  CV pipeline / mock
        |  bus.publish(event)
        v
  backend/app/bus.py ----------------> /events/stream, /summary (unchanged)
        |  open_subscription()
        v
  backend/app/actors.py (bridge)
    bounded buffer, in-order delivery, retry with backoff, heartbeats
        |                                     |
        | Camera(cam).record(event, site)     | Camera(cam).heartbeat(fps, site)
        v                                     v
  +------------------------------------------------------+
  | Camera "cam-1-loading-dock"   (one per camera)        |
  |   persisted: status online/offline, fps,              |
  |              last_frame_at, last_event, events_seen   |
  |   durable alarm: no contact for 15s -> offline        |
  +------------------------------------------------------+
        | Site(site).record(event)            | Site(site).camera_changed(info)
        v                                     v   (only on online/offline flips)
  +------------------------------------------------------+
  | Site "demo-warehouse"         (one per warehouse)     |
  |   persisted: newest 1000 incidents (+ ack state),     |
  |              seen ids (idempotency), lifetime counters |
  |              by zone/camera/type/severity, camera     |
  |              health, version                          |
  |   computed: 24h compliance score, open alerts         |
  |   record / acknowledge / snapshot / summary(hours)    |
  +------------------------------------------------------+
        | broadcast SiteMessage on every change
        v
  dashboard sockets (Jas), watch_site.py, anything else
```

## Why actors

- **Serialized updates from many cameras.** Each actor instance runs one call at a time (no
  method is `@interleave`). When every camera in a warehouse reports at once, their
  `Site.record` calls queue at the Site and apply one after another, each seeing the state the
  previous one committed. Counters never lose an increment and the incident list never drops a
  concurrent append, with no locks or database transactions in our code.
  `tests/test_live.py::test_concurrent_cameras_lose_no_updates` fires 10 cameras x 20 events
  from 10 threads and checks every counter is exact (200 total, 20 per camera).
- **Durable state across restarts.** Persisted fields commit atomically after each successful
  call (a failed call rolls back). Killing and restarting the actors server keeps every
  incident, counter, acknowledgement and camera status.
- **Exactly-once effect over an unreliable link.** The bridge delivers at least once (it
  retries, including after `outcome_unknown` where the call may already have run) and
  `Site.record` is idempotent by event id, so a retry never double counts.
- **Live fan-out.** The Site pushes a consistent snapshot to every connected socket on every
  change. Dashboards never poll and never see half-applied state.
- **Camera health without polling.** Each Camera arms a durable alarm on every contact; if
  heartbeats stop, the runtime wakes it to mark itself offline and tell the Site, even if
  nothing else ever calls it.

## Actor API

`Site(id)`:

| Method | Returns | Notes |
| --- | --- | --- |
| `record(event)` | `{event_id, duplicate, total}` | idempotent by `event.id` |
| `acknowledge(event_id, by)` | `Incident` or `null` | `null` if unknown or already acknowledged |
| `snapshot()` | `SiteSnapshot` | same object the socket sends |
| `summary(hours=24)` | same shape as backend `GET /summary` (no `mode`) | over the stored newest 1000 incidents |
| `camera_changed(info)` | | called by Camera actors |

`Camera(id)`: `heartbeat(fps, site)`, `record(event, site)` (forwards to `Site(site).record`),
`get_status()`.

Score: weights low 1, medium 3, high 5; `weighted_per_hour = sum(weights in window) / window_hours`;
`score = clamp(100 - 2 * weighted_per_hour, 0, 100)`; window = oldest event in range to now,
at least 1h, at most `hours`. Identical to `backend/app/main.py` `/summary`.

## Socket messages (for the dashboard)

Get a socket grant from a backend route (TypeScript: `actors.Site.prepareWebsocket({actorId:
"demo-warehouse", metadata: {name: "dashboard"}})`, Python: `async_actors.Site.prepare_websocket(...)`),
then open `grant.websocketUrl`. Metadata is `{"name": "<viewer name>"}`.

Every message the Site sends is JSON of this shape. It is always a full snapshot, so the client
can just replace its state (drop messages whose `snapshot.version` is lower than the last one):

```json
{
  "type": "snapshot",
  "reason": "connect | refresh | recorded | acknowledged | camera",
  "event_id": "evt_20261031_184936_2_0002",
  "snapshot": {
    "site": "demo-warehouse",
    "version": 42,
    "updated_at": "2026-10-31T18:49:36.912Z",
    "total": 37,
    "by_zone": {"loading-dock": 10},
    "by_camera": {"cam-1-loading-dock": 10},
    "by_type": {"unsafe_lift": 12},
    "by_severity": {"high": 18, "medium": 11, "low": 8},
    "compliance_score": 82.0,
    "weighted_incidents_per_hour": 9.0,
    "window_hours": 1.0,
    "open_alerts_count": 37,
    "open_alerts": [
      {
        "event": { "...": "SaesrEvent, see contract/event.schema.json" },
        "recorded_at": "2026-10-31T18:49:36.900Z",
        "acknowledged_by": null,
        "acknowledged_at": null
      }
    ],
    "recent": [ "same Incident shape, newest first, up to 20" ],
    "cameras": {
      "cam-1-loading-dock": {"camera": "cam-1-loading-dock", "online": true, "fps": 12.5, "last_seen_at": "..."}
    }
  }
}
```

- `total` and the `by_*` counters are lifetime totals. `compliance_score` and its inputs are the last 24h.
- `open_alerts` holds the newest 50 unacknowledged incidents, newest first; `open_alerts_count` is exact.
- `event_id` is set for `recorded` and `acknowledged`, null otherwise.

The socket also accepts:

```json
{"type": "ack", "event_id": "evt_...", "by": "jas"}   // acknowledge; every socket gets the new snapshot
{"type": "refresh"}                                   // this socket gets a fresh snapshot
```

A raw text `ping` is answered `pong` by the gateway without waking the actor (keepalive).
`GET /site` on the backend returns the same snapshot object plus `"source"`.

## Tests

```sh
cd durable && uv run pytest     # logic tests always; live tests need `durable-actors dev` running
cd backend && uv run pytest tests/test_actors_bridge.py
```
