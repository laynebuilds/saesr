"""Post mock v2 events to the saesr API, optionally driving a full incident cycle.

For building the dashboard with no CV running. Default: one event of each type to POST /events
(with placeholder keyframes), plus a duplicate event_id and a compliant_observation.

    cd backend
    uv run python scripts/mock_events.py                       # one of each type
    uv run python scripts/mock_events.py --cycle               # full incident cycle, resolves
    uv run python scripts/mock_events.py --cycle --recurring   # repeat violation instead
    uv run python scripts/mock_events.py --base-url http://192.168.1.20:8000

--cycle runs a STAND-IN RELAY FOR TESTING ONLY (Jas's real relay service replaces it). It serves
POST /relay/start on --relay-port, so start the backend with RELAY_URL=http://127.0.0.1:8100 and
the camera actor will call it. For each incident it replies to
POST /internal/cameras/{camera_id}/message with relay_update triage (confirmed, severity 4) ->
coach -> fixer (action_updates: assign_coaching done, open_corrective_record done, slow_machine
awaiting_approval) -> verifier -> reporter, each with a trace entry. Then it posts a
compliant_observation in the same zone so the incident resolves (or, with --recurring, a repeat
violation). If no /relay/start arrives (RELAY_URL not set), it finds the incident in
GET /cameras/{id}/state and starts the chain itself. When the actor asks it to execute an approved
action (RelayStart.action), it calls POST /mock/machine/slow and reports the action done.
"""
import argparse
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.schema import RelayStart  # noqa: E402

STEPS = ("triage", "coach", "fixer", "verifier", "reporter")
MODEL = "stand-in"

# type -> (camera_id, zone_id, sample still/clip stem in /samples)
DEMO = {
    "unsafe_lift": ("cam-01", "zone-pack-line", "unsafe_lift"),
    "zone_breach": ("cam-01", "zone-machine-a", "restricted_zone"),
    "missing_hairnet": ("cam-02", "zone-handwash", "no_hairnet"),
    "missing_gloves": ("cam-02", "zone-handwash", "no_gloves"),
}
METRICS = {
    "unsafe_lift": {"torso_angle_deg": 62, "duration_s": 1.4},
    "zone_breach": {"dwell_s": 3.2},
    "missing_hairnet": {"frames_seen": 24},
    "missing_gloves": {"frames_seen": 18},
    "compliant_observation": {"frames_seen": 30},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---- payloads (pure, tested) ----------------------------------------------------------------


def event_payload(type_: str, camera_id: Optional[str] = None, zone_id: Optional[str] = None,
                  base_url: str = "", event_id: Optional[str] = None) -> dict:
    cam, zone, stem = DEMO.get(type_, ("cam-01", "zone-pack-line", "no_vest"))
    still = f"{base_url.rstrip('/')}/samples/{stem}.jpg"
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "camera_id": camera_id or cam,
        "type": type_,
        "confidence": 0.91 if type_ != "compliant_observation" else 0.88,
        "timestamp": now_iso(),
        "zone_id": zone_id or zone,
        "bbox": [0.42, 0.3, 0.16, 0.45],
        "centroid": {"x": 0.5, "y": 0.52},
        "metrics": METRICS.get(type_, {}),
        "keyframes": [still, still, still],
        "clip_url": f"{base_url.rstrip('/')}/samples/{stem}.mp4",
    }


def demo_events(base_url: str = "") -> list[dict]:
    """One of each violation, a duplicate of the first (same event_id), and a compliant_observation."""
    events = [event_payload(t, base_url=base_url) for t in DEMO]
    events.append(dict(events[0]))  # exact duplicate: the API and actor must drop it
    events.append(event_payload("compliant_observation", "cam-01", "zone-pack-line", base_url))
    return events


def _trace(step: str, started: str, summary: str) -> dict:
    return {"agent": step, "model": MODEL, "started_at": started, "ended_at": now_iso(), "output_summary": summary}


def _action(incident_id: str, kind: str, status: str, params: Optional[dict] = None) -> dict:
    return {
        "action_id": f"{incident_id}-{kind}",
        "incident_id": incident_id,
        "kind": kind,
        "status": status,
        "params": params or {},
        "executed_at": now_iso() if status == "done" else None,
    }


def step_messages(step: str, incident_id: str, event: dict) -> list[dict]:
    """ActorMessages the stand-in relay sends for one step of an incident."""
    started = now_iso()
    type_, zone = event.get("type", "unsafe_lift"), event.get("zone_id", "zone")
    hygiene = type_ in ("missing_hairnet", "missing_gloves")

    def update(status: str, output: dict, summary: str) -> dict:
        return {"kind": "relay_update", "incident_id": incident_id, "step": step, "status": status,
                "output": output, "trace": _trace(step, started, summary)}

    if step == "triage":
        return [update("confirmed", {"verdict": "confirmed", "severity": 4,
                                     "reason": f"{type_.replace('_', ' ')} clearly visible in all 3 keyframes"},
                       "confirmed, severity 4")]
    if step == "coach":
        tip = {
            "unsafe_lift": "Bend at the knees, keep the load close, and ask for a two-person lift above 23 kg.",
            "zone_breach": "Stay behind the yellow line while machine A is running; use the walkway.",
            "missing_hairnet": "Hairnet on before entering the handwash zone, every time.",
            "missing_gloves": "Gloves on after handwashing, before touching product.",
        }.get(type_, "Follow the site procedure for this zone.")
        return [update("done", {"message": tip, "channel": "floor_display"}, "coaching message drafted")]
    if step == "fixer":
        actions = [
            _action(incident_id, "assign_coaching", "done", {"module": f"{type_}-refresher", "zone_id": zone}),
            _action(incident_id, "open_corrective_record", "done",
                    {"description": f"{type_.replace('_', ' ')} in {zone}", "standard_tag": "HACCP" if hygiene else "OSHA 1910.176"}),
        ]
        if hygiene:
            actions.append(_action(incident_id, "flag_batch_hold", "done", {"line": zone}))
        else:
            actions.append(_action(incident_id, "slow_machine", "awaiting_approval",
                                   {"machine_id": "machine-a", "zone_id": zone, "speed_pct": 50}))
        msgs = [{"kind": "action_update", "incident_id": incident_id, "action": a} for a in actions]
        kinds = ", ".join(f"{a['kind']} ({a['status']})" for a in actions)
        return msgs + [update("done", {"actions": [a["action_id"] for a in actions]}, kinds)]
    if step == "verifier":
        return [update("done", {"watching": zone, "window_s": 120}, f"watching {zone} for a compliant observation")]
    if step == "reporter":
        return [update("done", {"report": f"{type_.replace('_', ' ')} in {zone}: confirmed, coached, corrective record opened."},
                       "incident report written")]
    raise ValueError(step)


def executed_action_messages(incident_id: str, action: dict) -> list[dict]:
    done = dict(action, status="done", executed_at=now_iso())
    return [{"kind": "action_update", "incident_id": incident_id, "action": done}]


# ---- HTTP -----------------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, client: Optional[httpx.Client] = None, token: Optional[str] = None):
        self.base = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)
        self.headers = {"X-Internal-Token": token} if token else {}
        self.lock = threading.Lock()  # TestClient and httpx.Client are not shared across threads safely

    def post(self, path: str, body: Any) -> httpx.Response:
        with self.lock:
            return self.client.post(self.base + path, json=body, headers=self.headers)

    def get(self, path: str) -> httpx.Response:
        with self.lock:
            return self.client.get(self.base + path)

    def post_event(self, event: dict) -> dict:
        r = self.post("/events", event)
        log(f"POST /events {event['type']:<22} {event['camera_id']} {event['zone_id']:<15} -> {r.status_code} {r.text[:120]}")
        return r.json() if r.status_code == 200 else {}

    def send(self, camera_id: str, msg: dict) -> None:
        r = self.post(f"/internal/cameras/{camera_id}/message", msg)
        label = msg.get("step") or msg.get("action", {}).get("kind", "")
        log(f"  relay -> {camera_id} {msg['kind']:<14} {label:<24} -> {r.status_code}")


class StandInRelay:
    """Stand-in for the relay service, for testing only."""

    def __init__(self, api: Api, step_delay: float = 2.0, after_cycle: Optional[Callable[[str, dict], None]] = None):
        self.api = api
        self.step_delay = step_delay
        self.after_cycle = after_cycle
        self.started: set[str] = set()
        self.done: set[str] = set()
        self.lock = threading.Lock()

    def handle_start(self, body: dict) -> dict:
        start = RelayStart.model_validate(body)
        event = start.event.model_dump(mode="json")
        if start.action is not None:
            threading.Thread(target=self.execute, args=(start, event), daemon=True).start()
            return {"ok": True, "executing": start.action.action_id}
        with self.lock:
            if start.incident_id in self.started:
                return {"ok": True, "already_running": True}
            self.started.add(start.incident_id)
        threading.Thread(target=self.run_chain, args=(start.incident_id, start.camera_id, event, start.step), daemon=True).start()
        return {"ok": True, "accepted": start.incident_id}

    def run_chain(self, incident_id: str, camera_id: str, event: dict, first: str = "triage") -> None:
        log(f"stand-in relay: incident {incident_id} on {camera_id}, starting at {first}")
        for step in STEPS[STEPS.index(first):]:
            time.sleep(self.step_delay)
            for msg in step_messages(step, incident_id, event):
                self.api.send(camera_id, msg)
        with self.lock:
            self.done.add(incident_id)
        if self.after_cycle:
            self.after_cycle(incident_id, event)

    def execute(self, start: RelayStart, event: dict) -> None:
        action = start.action.model_dump(mode="json")
        log(f"stand-in relay: executing approved {action['kind']} for {start.incident_id}")
        if action["kind"] == "slow_machine":
            r = self.api.post("/mock/machine/slow", {"incident_id": start.incident_id, **action.get("params", {})})
            log(f"  POST /mock/machine/slow -> {r.status_code} {r.text[:100]}")
        for msg in executed_action_messages(start.incident_id, action):
            self.api.send(start.camera_id, msg)

    def serve(self, port: int) -> ThreadingHTTPServer:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path.rstrip("/") != "/relay/start":
                    self.send_error(404)
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    out, code = relay.handle_start(body), 200
                except Exception as err:  # bad payload: tell the actor, keep serving
                    out, code = {"ok": False, "error": str(err)[:300]}, 422
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def find_incident(api: Api, camera_id: str, event_id: str, timeout: float) -> Optional[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/cameras/{camera_id}/state")
        if r.status_code == 200:
            for inc in r.json().get("incidents", []):
                if event_id in inc.get("event_ids", []):
                    return inc["incident_id"]
        time.sleep(0.5)
    return None


def run_once(api: Api) -> list[dict]:
    results = [api.post_event(e) for e in demo_events(api.base)]
    dupes = sum(1 for r in results if r.get("duplicate"))
    log(f"posted {len(results)} events ({dupes} duplicate dropped by the API)")
    return results


def run_cycle(api: Api, args) -> None:
    finished = threading.Event()

    def after(incident_id: str, event: dict) -> None:
        time.sleep(args.resolve_after)
        if args.recurring:
            log(f"posting a REPEAT {event['type']} in {event['zone_id']} (incident should go recurring)")
            api.post_event(event_payload(event["type"], event["camera_id"], event["zone_id"], api.base))
        else:
            log(f"posting compliant_observation in {event['zone_id']} (incident should resolve)")
            api.post_event(event_payload("compliant_observation", event["camera_id"], event["zone_id"], api.base))
        finished.set()

    relay = StandInRelay(api, args.step_delay, after)
    server = relay.serve(args.relay_port)
    log(f"STAND-IN relay (testing only) on http://127.0.0.1:{args.relay_port}/relay/start; "
        f"start the backend with RELAY_URL=http://127.0.0.1:{args.relay_port}")
    try:
        event = event_payload(args.type, base_url=api.base)
        api.post_event(event)
        if not args.no_self_start:
            time.sleep(args.self_start_after)
            if not relay.started:
                incident_id = find_incident(api, event["camera_id"], event["event_id"], timeout=10)
                if incident_id is None:
                    log("no /relay/start and no incident in GET /cameras/{id}/state (actors off?); giving up")
                    return
                log(f"no /relay/start received; driving incident {incident_id} directly")
                with relay.lock:
                    relay.started.add(incident_id)
                threading.Thread(target=relay.run_chain, args=(incident_id, event["camera_id"], event), daemon=True).start()
        finished.wait(timeout=args.step_delay * 10 + args.resolve_after + 60)
        if args.hold:
            log("cycle done; stand-in relay still serving (approvals execute here). Ctrl-C to stop.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", default=os.environ.get("SAESR_API", "http://127.0.0.1:8000"))
    p.add_argument("--cycle", action="store_true", help="drive a full incident cycle with a stand-in relay")
    p.add_argument("--recurring", action="store_true", help="with --cycle: post a repeat violation instead of resolving")
    p.add_argument("--type", default="unsafe_lift", choices=list(DEMO), help="violation for --cycle")
    p.add_argument("--relay-port", type=int, default=8100)
    p.add_argument("--step-delay", type=float, default=2.0, help="seconds between relay steps")
    p.add_argument("--resolve-after", type=float, default=6.0, help="seconds after the reporter before the follow-up event")
    p.add_argument("--self-start-after", type=float, default=3.0, help="start the chain ourselves if no /relay/start by then")
    p.add_argument("--no-self-start", action="store_true", help="only react to /relay/start from the actor")
    p.add_argument("--hold", action="store_true", help="keep the stand-in relay serving after the cycle")
    p.add_argument("--internal-token", default=os.environ.get("INTERNAL_TOKEN"))
    args = p.parse_args(argv)
    if args.recurring:
        args.cycle = True
    api = Api(args.base_url, token=args.internal_token)
    try:
        api.get("/health").raise_for_status()
    except httpx.HTTPError as err:
        log(f"API not reachable at {args.base_url}: {err}")
        return 1
    if args.cycle:
        run_cycle(api, args)
    else:
        run_once(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
