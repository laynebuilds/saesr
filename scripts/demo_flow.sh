#!/usr/bin/env bash
# One-command demo driver: replays real lift clips on cam-01 so the actors, the dashboard and the
# people alerts (manager and worker texts, manager phone call) fire in order.
#
#   scripts/demo_flow.sh [resolved|recurring|both]     (default both)
#
# resolved   bad lift -> wait -> good lift: incident resolves, manager gets the resolved text
# recurring  bad lift -> wait -> bad lift again: URGENT text and phone call to the manager
# both       recurring, wait 25 s, then resolved
#
# Env: API (default http://localhost:8000), WAIT_FIX (default 22), ADMIN_TOKEN (if the API needs it).
# Expects the demo stack to be running already (scripts/run_demo.sh with the live extra).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${API:-http://localhost:8000}"
WAIT_FIX="${WAIT_FIX:-22}"
CAM="cam-01"
BAD="$ROOT/samples/real/bad_lift.mp4"
GOOD="$ROOT/samples/real/good_lift.mp4"
MODE="${1:-both}"

die() { echo "error: $*" >&2; exit 1; }

case "$MODE" in
  resolved|recurring|both) ;;
  *) die "usage: $0 [resolved|recurring|both]" ;;
esac

for f in "$BAD" "$GOOD"; do
  [ -f "$f" ] || die "missing clip: $f"
done
command -v curl >/dev/null 2>&1 || die "curl not found"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
curl -fsS --max-time 5 "$API/health" >/dev/null 2>&1 || die "API not reachable at $API/health (start the demo stack first)"

header() {
  echo
  echo "==== [$(date '+%H:%M:%S')] $* ===="
}

replay() {
  local src="$1"
  local auth=()
  if [ -n "${ADMIN_TOKEN:-}" ]; then auth=(-H "X-Admin-Token: $ADMIN_TOKEN"); fi
  curl -fsS --max-time 10 -X POST "$API/admin/replay" \
    -H "Content-Type: application/json" ${auth[@]+"${auth[@]}"} \
    -d "{\"camera_id\":\"$CAM\",\"source\":\"$src\",\"loop\":false}" >/dev/null \
    || die "POST $API/admin/replay failed for $src"
  echo "replaying $(basename "$src") on $CAM"
}

countdown() {
  local n="$1" label="$2"
  while [ "$n" -gt 0 ]; do
    printf "\r%s in %2ds " "$label" "$n"
    sleep 1
    n=$((n - 1))
  done
  printf "\r%s now.        \n" "$label"
}

statuses() {
  echo "incidents on $CAM:"
  curl -fsS --max-time 5 "$API/cameras/$CAM/state" 2>/dev/null | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except Exception:
    print("  (state unavailable)"); sys.exit(0)
incs = s.get("incidents") or []
if not incs:
    print("  (none)")
for i in incs:
    print("  %-12s  %-16s  %s" % ((i.get("incident_id") or "?")[:12], i.get("type"), i.get("status")))
' || echo "  (state unavailable)"
}

flow_resolved() {
  header "RESOLVED 1/2: bad lift"
  replay "$BAD"
  echo "expect: manager alert text, worker coaching text, agents on the dashboard"
  countdown "$WAIT_FIX" "good lift"
  statuses
  header "RESOLVED 2/2: good lift"
  replay "$GOOD"
  echo "expect: incident resolves, manager gets resolved text"
  countdown 15 "status check"
  statuses
}

flow_recurring() {
  header "RECURRING 1/2: bad lift"
  replay "$BAD"
  echo "expect: manager alert text, worker coaching text, agents on the dashboard"
  countdown "$WAIT_FIX" "repeat bad lift"
  statuses
  header "RECURRING 2/2: bad lift again"
  replay "$BAD"
  echo "expect: URGENT text and phone call to the manager"
  countdown 15 "status check"
  statuses
}

case "$MODE" in
  resolved) flow_resolved ;;
  recurring) flow_recurring ;;
  both)
    flow_recurring
    header "pause before the resolved flow"
    countdown 25 "resolved flow"
    flow_resolved
    ;;
esac

header "demo done"
