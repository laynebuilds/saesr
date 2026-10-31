# saesr durable actors (contract v2)

The live state behind saesr lives in [Durable Actors](https://github.com/TerseAI/durable-actors)
(`src/actors.py`): one `Camera` actor per camera (`cam-01`, `cam-02`) and one `Site` actor
(`site-demo`). The backend talks to them only through `backend/app/actors.py`; the relay talks to
the backend; dashboards open WebSockets straight to the actors.

## Run it

Requires Python 3.11+ and uv. No Bun needed.

```sh
# terminal 1: actors server (state kept in durable/.durable-actors/)
cd durable
uv run durable-actors dev                              # http://127.0.0.1:7100

# terminal 2: backend with the bridge on
cd backend
ACTORS_URL=http://127.0.0.1:7100 RELAY_URL=http://127.0.0.1:8787 \
  uv run --extra actors uvicorn app.main:app --port 8000

# terminal 3 (optional): watch the SiteState stream a dashboard gets
cd durable && uv run python scripts/watch_site.py

# inspect
uv run durable-actors inspect Camera/cam-01
```

| Variable | Where | Default | Meaning |
| --- | --- | --- | --- |
| `ACTORS_URL` (or `DURABLE_ACTORS_CONTROL_PLANE_URL`) | backend | unset = bridge off | actors server |
| `RELAY_URL` | backend | unset = relay calls stay pending (logged once) | relay base URL; the bridge POSTs `{RELAY_URL}/relay/start` |
| `ACTORS_HEARTBEAT_S` | backend | `5` | heartbeat per camera |
| `RELAY_DISPATCH_S` | backend | `1` | outbox poll interval (also woken right after any message that queues a call) |
| `ACTORS_BUFFER_MAX` | backend | `1000` | detections held while the actors server is down (oldest dropped) |
| `VERIFY_WINDOW_S` | actors server | `20` | verification window after the last action finishes |
| `CAMERA_OFFLINE_S` | actors server | `15` | no heartbeat or detection for this long = offline |
| `RECOVERING_S` | actors server | `3` | how long a reloaded camera shows `recovering` |

After changing an actor method or model: `./scripts/sync_client.sh` (regenerates `generated/`
and vendors it into `backend/app/actors_generated/`).

## The actor model

```
 CV worker / mock --bus--> backend/app/actors.py bridge --+-- Camera(cam).message(detection)    in order, buffered, retried
                                                          +-- Camera(cam).message(heartbeat)    every 5 s
                                                          +-- relay dispatcher: Camera.pending_relay() -> POST RELAY_URL/relay/start
                                                          |                     2xx -> Camera.relay_delivered(id)
 relay (Jas) --POST /internal/cameras/{id}/message--> actors.send(cam, ActorMessage) --> Camera(cam).message(...)

 +----------------------------------------------------------------------+
 | Camera "cam-01"   persisted CameraCore                                |
 |   incidents (newest 50), counters, false_positives_filtered,         |
 |   autonomous_fixes, seenEventIds (1000), version,                     |
 |   outbox of pending relay calls (stable ids), delivered ids,          |
 |   verification deadlines, last contact, boot id                      |
 |   ONE durable alarm = earliest of: offline timeout, end of            |
 |   recovering, any verification deadline                              |
 +----------------------------------------------------------------------+
        | on every change: broadcast CameraState to its sockets
        | and Site("site-demo").update_camera(CameraState)
        v
 +----------------------------------------------------------------------+
 | Site "site-demo"  persisted latest CameraState per camera             |
 |   broadcast SiteState (roll-up) to its sockets on every change        |
 +----------------------------------------------------------------------+
```

- **One writer at a time.** No method is `@interleave`, so a detection, a relay update and an
  approval for the same camera run one after another, each seeing the last commit. No locks, no
  extra queues.
- **Atomic, durable commits.** Persisted fields commit after each successful call; a failing call
  rolls back. Killing the actors server loses nothing that was acknowledged.
- **version** increments once per call that changes anything. A call that changes nothing
  (duplicate event, re-sent update) leaves `version` alone and returns `changed: false`.
- **Recovery.** The runtime has no rehydrate hook in 0.14.6, so each actor process gets a random
  `BOOT_ID` at import, and the camera persists the boot id it last ran under. The first call in a
  new process (any message, read, or the durable alarm) sees a different boot id: it sets
  `recovered_from_version = version`, `status = "recovering"`, and an alarm flips it to `online`
  `RECOVERING_S` later (or `offline` if no heartbeat arrived). After a SIGKILL, each actor answers
  `unavailable` for a moment until its old owner lease expires; the bridge just retries.

## Camera state machine

Messages are `schema.ActorMessage` (`kind`: `detection | relay_update | action_update | approve | close | heartbeat`).

**detection**
- Dedupe by `event_id` (bounded memory of 1000): same id twice is a no-op.
- `counters[type] += 1` for every new event, including `compliant_observation`.
- `compliant_observation` never opens an incident. It resolves every `actioned` incident in the
  same zone whose window is still open (`resolved`, `low_confidence: false`).
- A violation matching an `actioned` incident (same type + zone, window open) makes it
  `recurring`, appends the event id, records a trace entry, and queues an escalation relay call
  (step `fixer`, `event.metrics.escalation = "recurring"`, outbox id `<incident>:fixer:r1`).
- Otherwise a violation within 10 s of the last event of an open incident (status `verifying`,
  `confirmed`, `unverified`, `awaiting_approval` or `recurring`, same type + zone) joins it.
- Otherwise a new incident: id `inc-<event_id>`, status `verifying`, `keyframes`/`clip_url` from
  the event, and a `triage` relay call is queued.

**Status flow**: `verifying -> confirmed | dismissed | unverified -> awaiting_approval (gated tier-2 action) -> actioned -> resolved | recurring -> closed`.

**relay_update** stores `output` plus `status` under `incident.relay[step]`, appends `trace`
(identical entries only once), then:
- `triage`: `output.severity` (1..5, clamped) sets severity. If still `verifying`: status
  `confirmed | dismissed | unverified` is applied as is, `failed` means `unverified`, anything
  else (`done`) means `confirmed`. `dismissed` increments `false_positives_filtered` (once) and
  stops the chain (pending calls for it are dropped).
- Every step queues the next one: `triage -> coach -> fixer -> verifier -> reporter`. A failed
  step still advances, so one bad agent never stalls the demo.
- `fixer` completing may move the incident to `actioned` (below).

**action_update** upserts by `action_id`:
- An action already `done` or `rejected` is never changed again: re-sending is a no-op, nothing
  is re-executed or counted twice. Re-sending an identical update is a no-op too.
- Tier 2 (`slow_machine`) cannot become `approved` or `done` without an `approve` message; such an
  update is stored as `awaiting_approval`. An `awaiting_approval` action moves the incident to
  `awaiting_approval`.
- `done` sets `executed_at` (if missing) and increments `autonomous_fixes` for tier 0, tier 1
  (both run without a human) and approved tier 2.
- When the `fixer` step has reported AND every action is `done | failed | rejected`, the incident
  becomes `actioned` and `verify_until = now + VERIFY_WINDOW_S` opens the verification window.

**approve** (`awaiting_approval` or `pending` action only; otherwise no-op): `status = approved`,
`approved_by = user`, and queues a relay call to execute it: step `fixer` with `action` set,
outbox id `<incident>:exec:<action_id>`.

**close**: `status = closed`, `closed_at` set, pending relay calls for it dropped. Closing twice is a no-op.

**heartbeat**: `last_heartbeat = now`; `offline -> online`. The alarm marks the camera `offline`
`CAMERA_OFFLINE_S` after the last heartbeat or detection.

**Verification window timeout** (durable alarm, no caller needed): an `actioned` incident whose
`verify_until` passes with neither a compliant observation nor a repeat becomes `resolved` with
`low_confidence: true`.

**compliance_score** (per camera, 0..100, one decimal): `100 - penalty`, severity defaults to 3
until triage sets it:

| incident status | penalty |
| --- | --- |
| verifying, confirmed, unverified, awaiting_approval | 4 x severity |
| recurring | 8 x severity |
| actioned (fix applied, being verified) | 2 x severity |
| resolved | 2 x severity, fading linearly to 0 over 10 minutes |
| dismissed, closed | 0 |

Site roll-up: `site_score` = mean of camera scores, `active_incidents` = incidents not
dismissed/resolved/closed across cameras, `autonomous_fixes` = sum.

## Relay protocol (for Jas)

### 1. The backend calls the relay

For every pending outbox item the bridge sends:

```
POST {RELAY_URL}/relay/start
Content-Type: application/json
X-Idempotency-Key: inc-evt-123:triage
```

```json
{
  "incident_id": "inc-evt-123",
  "camera_id": "cam-01",
  "step": "triage",
  "event": {
    "event_id": "evt-123", "site_id": "site-demo", "camera_id": "cam-01", "type": "unsafe_lift",
    "confidence": 0.91, "timestamp": "2026-10-31T19:20:00Z", "zone_id": "zone-pack-line",
    "bbox": [0.4, 0.2, 0.2, 0.5], "centroid": {"x": 0.5, "y": 0.45},
    "metrics": {"torso_angle_deg": 62}, "keyframes": ["https://.../1.jpg", "https://.../2.jpg", "https://.../3.jpg"],
    "clip_url": "https://.../clip.mp4"
  },
  "keyframes": ["https://.../1.jpg", "https://.../2.jpg", "https://.../3.jpg"],
  "clip_url": "https://.../clip.mp4",
  "action": null
}
```

- Answer **2xx as soon as you have accepted the job** (do the work async). Only then is the item
  marked delivered. Anything else, or no answer, and it is resent on the next pass (every
  ~1 s, backing off), including after a backend restart or an actors-server restart.
- **Dedupe on `X-Idempotency-Key`** (the outbox id): the same key can arrive more than once (for
  example the backend died after your 2xx but before it told the actor). If you have seen the
  key, answer 2xx and do nothing.
- Key shapes: `<incident>:<step>` (normal chain), `<incident>:fixer:r<N>` (recurring escalation N:
  `event.metrics.escalation == "recurring"`; raise the supervisor alert + work order),
  `<incident>:exec:<action_id>` (execute an approved gated action; `action` is set, `step` is `fixer`).

### 2. The relay reports back

`POST /internal/cameras/{camera_id}/message` on the backend with one `ActorMessage` per call
(the backend route calls `actors.send(camera_id, msg)`). Every message is safe to resend.

Triage result (status `confirmed | dismissed | unverified`, or `failed`):

```json
{"kind": "relay_update", "incident_id": "inc-evt-123", "step": "triage", "status": "confirmed",
 "output": {"severity": 4, "reason": "back bent 62 degrees under load"},
 "trace": {"agent": "triage", "model": "claude-...", "started_at": "2026-10-31T19:20:01Z",
           "ended_at": "2026-10-31T19:20:03Z", "output_summary": "confirmed, severity 4"}}
```

Later steps (`coach`, `fixer`, `verifier`, `reporter`), status `done` or `failed`:

```json
{"kind": "relay_update", "incident_id": "inc-evt-123", "step": "coach", "status": "done",
 "output": {"message": "Bend your knees..."}, "trace": {"agent": "coach", "started_at": "..."}}
```

The fixer reports each action it takes (send them as soon as you create them, `pending` first is fine):

```json
{"kind": "action_update", "incident_id": "inc-evt-123",
 "action": {"action_id": "act-1", "incident_id": "inc-evt-123", "kind": "assign_coaching",
            "status": "done", "params": {"worker": "W-12"}, "executed_at": "2026-10-31T19:20:05Z"}}
```

A gated tier-2 action is proposed, not executed:

```json
{"kind": "action_update", "incident_id": "inc-evt-123",
 "action": {"action_id": "act-2", "incident_id": "inc-evt-123", "kind": "slow_machine",
            "status": "awaiting_approval", "params": {"machine": "A", "pct": 50}}}
```

Then send the fixer's own `relay_update` (`step: "fixer"`, `status: "done"`). The incident goes
`actioned` once the fixer step has reported and every action is finished, in either order.

Dashboard approval (via the backend route) and the follow-up:

```json
{"kind": "approve", "incident_id": "inc-evt-123", "action_id": "act-2", "user": "jas"}
```

The relay then receives `/relay/start` with key `inc-evt-123:exec:act-2` and `action.status == "approved"`;
after executing it sends the `action_update` with `status: "done"` (counted once, ever).

Other messages: `{"kind": "close", "incident_id": "...", "reason": "handled"}`,
`{"kind": "heartbeat", "fps": 12.0}`, `{"kind": "detection", "event": {...}}`.

Full happy-path sequence for one incident:

```
actor  -> relay   start triage                   (inc:triage)
relay  -> actor   relay_update triage confirmed
actor  -> relay   start coach                    (inc:coach)
relay  -> actor   relay_update coach done
actor  -> relay   start fixer                    (inc:fixer)
relay  -> actor   action_update act-1 done       (+ act-2 awaiting_approval if gated)
relay  -> actor   relay_update fixer done
actor  -> relay   start verifier                 (inc:verifier)
             ... incident actioned once all actions finished: verification window opens
relay  -> actor   relay_update verifier done
actor  -> relay   start reporter                 (inc:reporter)
relay  -> actor   relay_update reporter done
camera -> actor   compliant_observation -> resolved | same violation -> recurring + (inc:fixer:r1) | timeout -> resolved low_confidence
```

## Dashboard sockets

Get a grant from a backend route (it calls `actors.socket_grant("camera" | "site", actor_id)`), then
open `websocketUrl` before `connectByMs`:

```json
{"websocketUrl": "ws://127.0.0.1:7100/v1/socket?key=...", "homeRegion": "...", "connectByMs": 1793474833558}
```

```js
const ws = new WebSocket(grant.websocketUrl);
ws.onmessage = (m) => { const s = JSON.parse(m.data); /* full state: replace, drop if older version */ };
ws.send(JSON.stringify({type: "refresh"}));   // optional: get the state again
ws.send("ping");                              // keepalive, answered "pong" without waking the actor
```

- A **Camera** socket receives a full `schema.CameraState` on connect and after every change
  (`version` strictly increases; drop anything lower than what you have).
- A **Site** socket (`site-demo`) receives a full `schema.SiteState` on connect and after every
  camera change:

```json
{"site_id": "site-demo", "site_score": 90.0, "active_incidents": 2, "autonomous_fixes": 1,
 "cameras": [
   {"camera_id": "cam-01", "site_id": "site-demo", "status": "online", "last_heartbeat": "2026-10-31T19:20:05.120Z",
    "version": 17, "recovered_from_version": null, "compliance_score": 92.0,
    "counters": {"unsafe_lift": 3, "compliant_observation": 1}, "false_positives_filtered": 1, "autonomous_fixes": 1,
    "incidents": [ { "incident_id": "inc-evt-123", "status": "actioned", "severity": 4, "event_ids": ["evt-123"],
                     "relay": {"triage": {"status": "confirmed", "severity": 4}}, "actions": [ ... ],
                     "trace": [ ... ], "verify_until": "2026-10-31T19:20:25.000Z", "low_confidence": false, "...": "..." } ]}
 ]}
```

## Backend interface (`backend/app/actors.py`)

`enabled()`, `start(cameras) -> Optional[Task]`, `stop()`, `async send(camera_id, msg) -> {"ok", "changed", "version", "state"} | {"ok": False, "error"}`,
`async camera_state(cam)`, `async site_state()`, `async socket_grant(kind, actor_id)`,
`open_state_subscription()` / `close_state_subscription(q)` (CameraState dicts, bounded,
drop-oldest, once per camera version; fed by `send()` results and a backend socket per camera),
`status()`.

## Crash and recover demo

1. Run the three terminals above; trigger an incident.
2. `Ctrl-C` the actors server mid-relay. The backend keeps serving; detections buffer.
3. Restart `uv run durable-actors dev`. About 2 s later the bridge has reconnected, resent the
   buffered detections and any un-acknowledged relay calls (same `X-Idempotency-Key`), and the
   camera shows `status: "recovering"` with `recovered_from_version`, then `online` 3 s later.

Use `Ctrl-C` (SIGINT/SIGTERM) on stage. After `kill -9` the state is just as safe, but each actor
answers `unavailable` until its previous owner lease expires: measured ~28 s before the camera
came back (vs 1.9 s after Ctrl-C). The bridge retries through it either way.

## Tests

```sh
cd durable && uv run pytest        # logic + contract parity + live (spawns its own server, SIGKILLs and restarts it)
cd backend && uv run --extra actors --group dev pytest -q tests/test_actors_*.py
                                   # bridge vs the real state machine in-process; test_actors_live.py needs a server on :7100
```
