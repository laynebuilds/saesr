#!/usr/bin/env bash
# Run the relay (triage, coach, fixer, verifier, reporter) on :8100. Start the stack with
#   RELAY_URL=http://127.0.0.1:8100 scripts/run_demo.sh
# Reads backend/.env (ANTHROPIC_API_KEY, SUPERVISOR_EMAIL, INTERNAL_TOKEN). Without SUPERVISOR_EMAIL,
# emails are written to backend/data/relay_outbox.jsonl instead of being sent.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -f "$ROOT/backend/.env" ]; then set -a; . "$ROOT/backend/.env"; set +a; fi
cd "$ROOT/backend" && exec uv run --extra relay --extra actors python -m relay
