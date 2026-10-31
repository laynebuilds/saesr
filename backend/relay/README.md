# saesr relay

The agent chain the camera actors call: **triage** (Claude Haiku 4.5, vision on the 3 keyframes),
**coach** and **fixer** (Claude Sonnet 5.5), **verifier** (Haiku), **reporter** (email to the supervisor).
Protocol: `durable/README.md`, "Relay protocol".

```bash
# backend/.env (gitignored)
ANTHROPIC_API_KEY=sk-ant-...
SUPERVISOR_EMAIL=you@example.com      # optional; unset = emails go to backend/data/relay_outbox.jsonl
# terminal 1
RELAY_URL=http://127.0.0.1:8100 scripts/run_demo.sh
# terminal 2
scripts/run_relay.sh                  # http://127.0.0.1:8100/health
```

- One `/relay/start` per step. Answers 2xx at once and dedupes on `X-Idempotency-Key`.
- **Fixer:** `policy.py` decides which actions are allowed. The model only fills in parameters, and extra actions are dropped. `action_id` is `sha1(incident_id:kind)[:16]`. Tier 0 and tier 1 actions run immediately: records go to `backend/data/relay_records.jsonl`, and the floor display is simulated. `slow_machine` waits for an approval, and the approved run calls `POST /mock/machine/slow` (simulated).
- **Recurring escalation** (`<incident>:fixer:r<N>`): the supervisor gets an email immediately.
- **Severity 4 to 5:** the supervisor is emailed as soon as triage confirms. The reporter then emails every outcome, as a full report for severity 4 to 5 and one line for severity 1 to 3.
- **Email** goes out through Apple Mail on this Mac using AppleScript, from the account that is signed in.
- **Without an API key** nothing stalls. Triage reports `unverified`. Coach, fixer and verifier fall back to the policy defaults and plain-text summaries.
- **Agent Relay:** with `RELAY_LOCAL=1` (or `RELAY_API_KEY`), each step also posts to `#safety-floor` as its own agent.
- **Timing:** latency and token counts for every step are in the trace and in `output`.
- **Prompts** are versioned in `prompts/v1/`.

Rehearsal: `uv run python -m relay.demo_event missing_hairnet --compliant 20` sends one detection whose keyframes
are real footage stills (the backend's own mock samples are synthetic, so triage correctly dismisses
them), then after 20 s a compliant observation in the same zone, which verifies the fix. Run the backend with a long `MOCK_EVERY` (e.g. 600) so its mock events do not mark the demo
incident recurring.

Measured on Oct 31 (real frames): triage 1.5 to 2.9 s (Haiku 4.5), coach 3.2 s and fixer about
2.6 s at low effort (Sonnet 5.5).
