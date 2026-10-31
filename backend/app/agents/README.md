# Saesr agents (Agent Relay)

Camera agents and a Safety Supervisor that talk to each other over [Agent Relay](https://agentrelay.com/docs).
Each camera is its own agent in the Relay workspace and reports what it sees to `#safety-floor`.
The supervisor reads that channel (never the backend bus), correlates across cameras and time,
and acts by messaging: escalation DMs to the `floor-manager`, patterns and a rolling shift brief
in the channel. Anyone in the workspace (a person on the Relay CLI, a Claude Code or Codex session,
the dashboard) can acknowledge incidents, and the supervisor stops paging for them.

## Setup (local, no account, about 1 minute)

```bash
# 1. Start a local Relay server (Node 22 or 24). Prints: workspace "local" on http://127.0.0.1:8787
npx -y agent-relay@next init --tools none

# 2. Run the backend with agents on. RELAY_LOCAL=1 reads the key `init` saved in ~/.agentworkforce/relay
cd backend
RELAY_LOCAL=1 MOCK_EVERY=2 uv run uvicorn app.main:app --port 8000

# 3. Watch the agents talk, and answer as the floor manager (second terminal)
RELAY_LOCAL=1 uv run python scripts/watch_relay.py
```

Optional LLM wording for escalations and the shift brief:

```bash
uv sync --extra agents
OPENAI_API_KEY=sk-... RELAY_LOCAL=1 uv run uvicorn app.main:app --port 8000
```

Without a key (or if the call fails or takes more than 8 s) the supervisor uses deterministic
text. The rules always decide *whether* to escalate; the LLM only writes *what the manager reads*.

Hosted workspace instead of local (agents on several machines): `npx -y agent-relay@next init --hosted`,
then run the backend with `RELAY_API_KEY=<workspace key> RELAY_BASE_URL=<url it printed>`.

When neither `RELAY_LOCAL` nor `RELAY_API_KEY` is set, the agent layer is off and the API is
exactly as before. If Relay is unreachable the API keeps serving; the agent layer logs, shows the
error in `/agents/activity` `status.error`, and reconnects with backoff (1, 2, 5, 10, 30 s).
Incidents that arrive during an outage are queued and posted after the reconnect.

## Roster and who talks to whom

```
                         backend bus (app/bus.py)
                                   |
          +------------+-----------+-----------+------------+
          v            v                       v            v
  cam-1-loading-dock  cam-2-forklift-lane  cam-3-packing  cam-4-cold-store     camera agents
          |            |                       |            |                (one Relay identity
          +------------+-----------+-----------+------------+                 per camera id)
                                   | post INCIDENT
                                   v
  +----------------------------- #safety-floor ------------------------------+
  |  INCIDENT ...   PATTERN ...   ESCALATION ...   SHIFT BRIEF ...   ACK ... |
  +--------------------------------------------------------------------------+
         | read                 ^ post                       ^ "ack evt_..."
         v                      |                            |
   safety-supervisor -----------+                     saesr-dashboard
         |   ^                                        (POST /agents/ack/{id})
   DM    |   |  DM "ack all" / "ack evt_..." / "status"
         v   |
     floor-manager   (human handle: Relay CLI, scripts/watch_relay.py, or a Claude Code session)
         
   safety-supervisor --> /agents/activity + /agents/stream (structured actions for the dashboard)
```

| Agent | Kind | Says | Listens to |
| --- | --- | --- | --- |
| `cam-*` (one per camera id) | agent | `INCIDENT` posts in `#safety-floor` | the backend bus, its own camera only |
| `safety-supervisor` | agent | `PATTERN`, `ESCALATION`, `SHIFT BRIEF`, `ACK` in the channel; escalation DMs | `#safety-floor`, its DMs |
| `floor-manager` | human | `ack ...`, `status` (DM to the supervisor or in the channel) | escalation DMs |
| `saesr-dashboard` | agent | `ack ...` in the channel when someone clicks ack in the UI | nothing |

Camera agents register the first time their camera reports, so mock mode (four cameras) and live
mode (one camera) need no roster config.

## Supervisor rules

All thresholds live in `config.py` (`Rules`) and can be overridden by env vars of the same name in
upper case, for example `ACK_TIMEOUT_S=15 BRIEF_EVERY_S=30`.

| Rule | Fires when | Default | Escalates (DM) |
| --- | --- | --- | --- |
| `zone_hotspot` | the same zone produces `ZONE_REPEAT_N` incidents within `ZONE_REPEAT_WINDOW_S` | 3 in 120 s | if any of them is high |
| `roaming_violator` | the same PPE violation (hard hat, vest, hairnet, gloves) on `ROAMING_MIN_CAMERAS` cameras within `ROAMING_WINDOW_S`: likely one worker moving across the floor | 2 cameras in 90 s | yes |
| `shift_spike` | `SPIKE_N` incidents of any kind within `SPIKE_WINDOW_S` | 8 in 60 s | yes |
| `unacked_high` | a high severity incident nobody acknowledged within `ACK_TIMEOUT_S`; overdue incidents batch into one page at most every `UNACKED_REPAGE_S` | 30 s, re-page 30 s | yes |
| shift brief | every `BRIEF_EVERY_S` while incidents keep coming, or on a `status` DM | 90 s | posted, not paged |

Repeats of the same finding (rule plus zone or type) are suppressed for `RULE_COOLDOWN_S` (90 s;
spikes `SPIKE_COOLDOWN_S`, 180 s). Windows run on event timestamps; the ack timeout runs on the
supervisor's clock. The event contract has no cross-camera person id, so "same person across
cameras" is inferred from the same PPE violation moving between cameras in a short window.

## Watching the conversation live (demo)

- **Floor manager console:** `RELAY_LOCAL=1 uv run python scripts/watch_relay.py` prints every
  channel post (cameras cyan, supervisor yellow) and every escalation DM (red). Type `ack all`,
  `ack evt_...` or `status` and it goes to the supervisor as a DM. It signs in as `floor-manager`.
  Use `--as viewer --read-only` for a second, view-only screen, `--raw` to see the machine lines.
- **Relay CLI as the human:** `npx -y agent-relay@next agent rotate floor-manager` (once, saves a
  token), then `npx -y agent-relay@next message inbox check --as floor-manager` and
  `npx -y agent-relay@next message dm send safety-supervisor "ack all" --as floor-manager`.
  `npx -y agent-relay@next message list safety-floor --as floor-manager` shows the channel.
- **A Claude Code session as floor manager:** run `npx -y agent-relay@next init` with Claude Code
  installed, open Claude Code, and ask it to read `#safety-floor` and acknowledge what it has
  handled by DMing `safety-supervisor`.
- **Dashboard:** `GET /agents/stream` (SSE) or poll `GET /agents/activity`.

## Message formats

Every message reads naturally in a channel and ends with one machine line, so agents never parse
prose.

Camera agent post:

```
INCIDENT HIGH unsafe_lift | zone loading-dock | cam-1-loading-dock | 18:57:29Z
back bent 74 degrees with straight legs while lifting (confidence 0.90)
saesr:event {"id":"evt_...","camera":"cam-1-loading-dock","type":"unsafe_lift","severity":"high",...}
```

`saesr:event` carries the full event exactly as in `contract/event.schema.json`.

Supervisor post (`PATTERN` when it only informs, `ESCALATION` when it also pages):

```
ESCALATION HIGH zone_hotspot | 3 incidents in forklift-lane within 2 min (2 unsafe_lift, 1 no_vest)
Action: Send a lead to forklift-lane now and pause the task that keeps triggering incidents until it is reviewed.
Paged @floor-manager.
saesr:action {"type":"escalate","rule":"zone_hotspot","severity":"high","event_ids":["evt_..."]}
```

Escalation DM to `floor-manager`:

```
[HIGH] 3 incidents in forklift-lane within 2 min (2 unsafe_lift, 1 no_vest).
Send a lead to forklift-lane now and pause the task that keeps triggering incidents until it is reviewed.
Reply 'ack evt_20261031_185701_2_0063' (or 'ack all') to acknowledge.
```

Acknowledgement (DM to the supervisor or post in the channel, case-insensitive):
`ack evt_...`, `ack evt_a evt_b`, `ack all`. The supervisor confirms in the channel:
`ACK 3 incidents acknowledged by floor-manager. 0 high severity still open.`

## API for the dashboard

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/agents/activity?limit=100&kind=action` | `{"status": {...}, "items": [...]}`, newest last; `kind` is `message` or `action` (optional) |
| GET | `/agents/stream` | SSE, `data: <item json>` per message or action, `: keepalive` every 15 s |
| POST | `/agents/ack/{event_id or all}?by=jas` | Acknowledges over Relay (as `saesr-dashboard`); 503 when agents are off |

`status`: `enabled`, `connected`, `relay_url`, `channel`, `agents` (roster), `error`, `llm`, `llm_note`.

Item:

```json
{
  "id": "act_000042",
  "ts": "2026-10-31T18:57:50.536+00:00",
  "kind": "action",
  "agent": "safety-supervisor",
  "to": "floor-manager",
  "text": "ESCALATION HIGH zone_hotspot | 3 incidents in forklift-lane ...",
  "event_id": "evt_20261031_185745_2_0011",
  "relay_id": null,
  "action": {
    "type": "escalate",
    "rule": "zone_hotspot",
    "severity": "high",
    "summary": "3 incidents in forklift-lane within 2 min (2 unsafe_lift, 1 no_vest)",
    "recommendation": "Send a lead to forklift-lane now and pause ...",
    "source": "rules",
    "event_ids": ["evt_...", "evt_...", "evt_..."],
    "zone": "forklift-lane",
    "cameras": ["cam-2-forklift-lane"],
    "types": ["no_vest", "unsafe_lift"],
    "notified": ["#safety-floor", "floor-manager"]
  }
}
```

`kind: "message"` items have `action: null` and `relay_id` set (the Relay message id).
`action.type` is one of `escalate`, `pattern`, `brief` (has `snapshot` with the shift numbers),
`ack` (has `by`). `action.source` is `llm` or `rules`.

## Files

- `config.py` Relay connection, agent names, every rule threshold
- `relay_client.py` async client for the Relay workspace HTTP API
- `camera.py` camera agents (one per camera id) fed from the bus
- `correlator.py` the supervisor's rules, pure and unit tested
- `supervisor.py` the Safety Supervisor agent: reads Relay, acts, records actions
- `llm.py` optional OpenAI wording
- `messages.py` wire formats
- `activity.py` the feed behind `/agents/activity` and `/agents/stream`
- `runtime.py` startup, reconnects with backoff
- `../../scripts/watch_relay.py` floor manager console for the demo

Why a small HTTP client instead of the Python SDK: `agent-relay-sdk` 14.0.0 (communicate mode)
wraps `relaycast-sdk` 1.0.0, whose models reject the current local server's responses (agent
`status: "active"`), so registering fails against a fresh `agent-relay init`. `relay_client.py`
calls the same documented endpoints. Swapping in the SDK later only touches that file.

## Tests

```bash
cd backend && uv run --group dev pytest -q tests/test_agents_correlator.py tests/test_agents_plumbing.py
```

Correlation rules run on synthetic event sequences; the plumbing tests drive the real runtime
(bus, camera agents, supervisor, reconnects, API) against an in-memory Relay (`tests/fake_relay.py`).
