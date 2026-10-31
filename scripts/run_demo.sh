#!/usr/bin/env bash
# Run the saesr demo stack: durable-actors dev server + backend API, each in a restart loop,
# so POST /admin/crash (or any crash) brings them back in about 1 to 2 seconds.
#
#   scripts/run_demo.sh                       # mock events, actors on
#   MODE=live SOURCE=demo.mp4 scripts/run_demo.sh
#   RELAY_URL=http://127.0.0.1:8100 scripts/run_demo.sh   # point actors at a relay (or the stand-in)
#
# Pid files: backend/data/actors.pid, backend/data/api.pid (read by /admin/crash?target=actors).
# Logs: backend/data/actors.log, backend/data/api.log (also echoed here). Ctrl-C stops everything.
# Works with macOS bash 3.2.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DATA="$ROOT/backend/data"
mkdir -p "$DATA"
STOP="$DATA/.run_demo.stop"
rm -f "$STOP"

# backend/.env (Supabase keys etc.) is gitignored; export it if present (SKIP_DOTENV=1 to skip).
if [ "${SKIP_DOTENV:-0}" != "1" ] && [ -f "$ROOT/backend/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/backend/.env"
  set +a
fi

ACTORS_PORT=${ACTORS_PORT:-7100}
API_PORT=${API_PORT:-8000}
RESTART_DELAY=${RESTART_DELAY:-1}
export ACTORS_URL=${ACTORS_URL:-http://127.0.0.1:$ACTORS_PORT}
export ACTORS_PID_FILE="$DATA/actors.pid"
export MODE=${MODE:-mock}
API_EXTRAS=${API_EXTRAS:---extra actors}
if [ "$MODE" = "live" ]; then
  API_EXTRAS="$API_EXTRAS --extra live"
fi
ACTORS_WATCH_FLAG=--no-watch
if [ "${ACTORS_WATCH:-0}" = "1" ]; then
  ACTORS_WATCH_FLAG=--watch
fi

say() { echo "[run_demo $(date +%H:%M:%S)] $*"; }

# kill a pid and all its descendants (uv run spawns a child python)
kill_tree() {
  _sig=$1
  _pid=$2
  for _c in $(pgrep -P "$_pid" 2>/dev/null); do
    kill_tree "$_sig" "$_c"
  done
  kill "-$_sig" "$_pid" 2>/dev/null
}

# restart_loop NAME DIR LOG CMD...: run CMD in DIR forever, pid in $DATA/NAME.pid
restart_loop() {
  _name=$1
  _dir=$2
  _log=$3
  shift 3
  while [ ! -f "$STOP" ]; do
    (cd "$_dir" && exec "$@") >>"$_log" 2>&1 &
    _child=$!
    echo "$_child" >"$DATA/$_name.pid"
    say "$_name started (pid $_child)"
    wait "$_child"
    _code=$?
    if [ -f "$STOP" ]; then
      break
    fi
    say "$_name exited with code $_code; restarting in ${RESTART_DELAY}s"
    sleep "$RESTART_DELAY"
  done
  rm -f "$DATA/$_name.pid"
}

LOOPS=""
TAIL_PID=""
cleanup() {
  trap - INT TERM EXIT
  touch "$STOP"
  say "stopping"
  for _n in api actors; do
    if [ -f "$DATA/$_n.pid" ]; then
      kill_tree TERM "$(cat "$DATA/$_n.pid")"
    fi
  done
  sleep 1
  for _n in api actors; do
    if [ -f "$DATA/$_n.pid" ]; then
      kill_tree KILL "$(cat "$DATA/$_n.pid")"
      rm -f "$DATA/$_n.pid"
    fi
  done
  for _p in $LOOPS $TAIL_PID; do
    kill "$_p" 2>/dev/null
  done
  wait 2>/dev/null
  rm -f "$STOP"
  say "stopped"
  exit 0
}
trap cleanup INT TERM EXIT

: >"$DATA/actors.log"
: >"$DATA/api.log"
tail -n 0 -F "$DATA/actors.log" "$DATA/api.log" 2>/dev/null &
TAIL_PID=$!

restart_loop actors "$ROOT/durable" "$DATA/actors.log" \
  uv run durable-actors dev --port "$ACTORS_PORT" $ACTORS_WATCH_FLAG &
LOOPS="$LOOPS $!"

# give the actors server a head start (the API buffers and retries anyway)
for _i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -o /dev/null "http://127.0.0.1:$ACTORS_PORT" 2>/dev/null; then
    break
  fi
  sleep 1
done

# shellcheck disable=SC2086
restart_loop api "$ROOT/backend" "$DATA/api.log" \
  uv run $API_EXTRAS uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" &
LOOPS="$LOOPS $!"

say "actors: $ACTORS_URL   api: http://127.0.0.1:$API_PORT   mode: $MODE   relay: ${RELAY_URL:-unset}"
say "crash test: curl -X POST 'http://127.0.0.1:$API_PORT/admin/crash?target=all'"

while [ ! -f "$STOP" ]; do
  sleep 1
done
