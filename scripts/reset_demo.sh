#!/usr/bin/env bash
# Wipe local demo state for a clean run: actor storage, the event log and saved clips.
# Stop scripts/run_demo.sh first. Supabase rows are not touched (clear them in the SQL editor).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if pgrep -f "durable-actors dev" >/dev/null; then
  echo "actors server still running; stop scripts/run_demo.sh first" >&2
  exit 1
fi
rm -rf "$ROOT/durable/.durable-actors"
rm -f "$ROOT/backend/data/events.jsonl"
rm -f "$ROOT/backend/clips/"*.mp4 "$ROOT/backend/clips/"*.jpg 2>/dev/null || true
echo "demo state cleared"
