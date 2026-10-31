"""iMessage alerts over Photon: the floor manager hears about every confirmed incident and its
outcome, and the worker gets the coaching note, straight from the camera actors' state.

Watches the actor state broadcasts (read only, like relay_feed) and sends one text per milestone:

    manager  confirmed (severity, what the camera saw, keyframe link), awaiting approval,
             recurring (escalation), resolved (verified or low confidence)
    worker   the coach agent's note

Off unless PHOTON_API_KEY, PHOTON_PROJECT_ID and at least one of MANAGER_PHONE / WORKER_PHONE are set.
Photon shared lines only reach someone who has texted the line first, so each phone must have sent
the line one message (any text). Sends are idempotent per incident and milestone; a failed send is
logged and retried on the next state broadcast, never blocking anything.
"""
import asyncio
import logging
import os
import re
from typing import Optional

import httpx

from . import actors

log = logging.getLogger("saesr.photon")

API = "https://api.photon.codes/v1"
TYPE_LABEL = {
    "unsafe_lift": "Unsafe lift", "zone_breach": "Person in a machine zone",
    "missing_hairnet": "Missing hairnet", "missing_gloves": "Missing gloves",
}
ZONE_LABEL = {"zone-pack-line": "pack line", "zone-machine-a": "machine A", "zone-handwash": "handwash station"}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return bool(_env("PHOTON_API_KEY") and _env("PHOTON_PROJECT_ID") and (_env("MANAGER_PHONE") or _env("WORKER_PHONE")))


def normalize(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone)


def _summary(value, keys=("summary", "message", "note", "text", "reason")) -> str:
    if isinstance(value, dict):
        for k in keys:
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k].strip()
    return value.strip() if isinstance(value, str) else ""


def _last_trace(inc: dict, agent: str) -> str:
    for t in reversed(inc.get("trace") or []):
        if t.get("agent") == agent and t.get("output_summary"):
            return t["output_summary"]
    return ""


def briefing(inc: dict) -> str:
    """The few-words escalation used for both the urgent text and the phone call."""
    issue = f"{TYPE_LABEL.get(inc.get('type'), inc.get('type')).lower()} again at the {ZONE_LABEL.get(inc.get('zone_id'), inc.get('zone_id'))} after coaching, injury risk"
    return (f"Worker number {_env('WORKER_NUMBER') or '1'}. Name: {_env('WORKER_NAME') or 'unknown'}. "
            f"Issue: {issue}. Action: stop the task and retrain now.")


def messages_for(state: dict, sent: set) -> list[tuple[str, str, str]]:
    """New (role, key, text) alerts for one CameraState. role is "manager" or "worker"."""
    out = []
    cam = state.get("camera_id", "?")
    for inc in state.get("incidents") or []:
        iid = inc.get("incident_id", "?")
        what = f"{TYPE_LABEL.get(inc.get('type'), inc.get('type'))} at the {ZONE_LABEL.get(inc.get('zone_id'), inc.get('zone_id'))} ({cam})"
        status = inc.get("status")
        relay = inc.get("relay") or {}
        frame = (inc.get("keyframes") or [None])[1 if len(inc.get("keyframes") or []) > 1 else 0]

        def add(role: str, milestone: str, text: str) -> None:
            key = f"{iid}:{milestone}:{role}"
            if key not in sent:
                out.append((role, key, text))

        sev = inc.get("severity")
        if sev and status not in ("verifying", "dismissed", "unverified"):
            seen = _summary(relay.get("triage"), ("reason", "summary")) or _last_trace(inc, "triage")
            lines = [f"saesr alert: {what}, severity {sev}/5.", seen]
            if frame and str(frame).startswith("http"):
                lines.append(f"Snapshot: {frame}")
            lines.append("The worker has been coached; I'll tell you if the fix holds.")
            add("manager", "confirmed", "\n".join(x for x in lines if x))
            coach = _summary(relay.get("coach"), ("recommended_fix", "summary")) or _last_trace(inc, "coach")
            if coach:
                add("worker", "coach", f"saesr safety coach: {coach}")
        if status == "awaiting_approval":
            add("manager", "approval", f"saesr: {what} needs your approval to slow the machine. Approve it on the dashboard.")
        if status == "recurring":
            add("manager", "recurring", "URGENT, saesr: " + briefing(inc))
        if status == "resolved":
            how = ("no repeat during the check window (low confidence)" if inc.get("low_confidence")
                   else "a correct lift was seen in the same zone, fix verified")
            add("manager", "resolved", f"saesr: {what} resolved: {how}.")
    return out


class Photon:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.project = _env("PHOTON_PROJECT_ID")
        self.client = client or httpx.AsyncClient(timeout=20)
        self.headers = {"authorization": f"Bearer {_env('PHOTON_API_KEY')}", "content-type": "application/json"}
        self.convos: dict[str, tuple[str, str]] = {}

    async def _req(self, method: str, path: str, **kw) -> dict:
        r = await self.client.request(method, f"{API}/projects/{self.project}{path}", headers={**self.headers, **kw.pop("headers", {})}, **kw)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"Photon {path} {r.status_code} {r.text[:200]}", request=r.request, response=r)
        return r.json() if r.content else {}

    async def conversation(self, phone: str) -> Optional[tuple[str, str]]:
        """(spaceId, senderId) for a phone that has texted the line: its direct space with an agent line. Cached."""
        want = normalize(phone)
        if want in self.convos:
            return self.convos[want]
        users = (await self._req("GET", "/users")).get("items") or []
        by_id = {u["id"]: u for u in users}
        human = next((u for u in users if u.get("kind") == "human" and normalize(u.get("handle", "")) == want), None)
        if not human:
            return None
        for space in (await self._req("GET", "/spaces")).get("items") or []:
            ids = space.get("participantIds") or []
            if human["id"] in ids:
                agent = next((i for i in ids if (by_id.get(i) or {}).get("kind") == "agent"), None)
                if agent:
                    self.convos[want] = (space["id"], agent)
                    return self.convos[want]
        return None

    async def send(self, phone: str, text: str, key: str) -> bool:
        convo = await self.conversation(phone)
        if not convo:
            log.warning("photon: %s has not texted the line yet, cannot reach them", phone[-4:].rjust(len(phone), "*"))
            return False
        await self._req("POST", "/messages", headers={"idempotency-key": key},
                        json={"spaceId": convo[0], "senderId": convo[1], "content": {"type": "text", "format": "plain", "text": text}})
        return True


async def run(client: Optional[httpx.AsyncClient] = None) -> None:
    phones = {"manager": _env("MANAGER_PHONE"), "worker": _env("WORKER_PHONE")}
    photon = Photon(client)
    sent: set = set()
    primed: set = set()
    q = actors.open_state_subscription()
    try:
        while True:
            state = await q.get()
            _last_states[state.get("camera_id")] = state
            if state.get("camera_id") not in primed:
                # first state after a (re)start: what already happened is history, only new milestones alert
                primed.add(state.get("camera_id"))
                sent.update(k for _, k, _ in messages_for(state, sent))
                continue
            for role, key, text in messages_for(state, sent):
                if not phones.get(role):
                    sent.add(key)
                    continue
                if key.endswith(":recurring:manager") and _env("PHOTON_CALL") == "1":
                    asyncio.create_task(_call_manager(phones[role], key))
                try:
                    if await photon.send(phones[role], text, key):
                        sent.add(key)
                        log.info("photon: sent %s", key)
                except (httpx.HTTPError, KeyError, ValueError) as err:
                    log.warning("photon: send %s failed (%s); will retry on the next update", key, err)
    finally:
        actors.close_state_subscription(q)


_called: set = set()


async def _call_manager(phone: str, key: str) -> None:
    """Phone the manager once per escalation with the spoken briefing; the urgent text goes regardless."""
    if key in _called:
        return
    _called.add(key)
    from . import voice_call

    inc_id = key.split(":")[0]
    state = [s for s in _last_states.values() for i in s.get("incidents") or [] if i.get("incident_id") == inc_id]
    inc = next(i for i in state[0]["incidents"] if i.get("incident_id") == inc_id) if state else {}
    text = "saesr safety alert. " + briefing(inc) + " Goodbye."
    try:
        log.info("calling the manager: %s", await voice_call.call(phone, text))
    except Exception as err:  # never let a phone problem touch detection
        log.warning("call to the manager failed: %s", err)


_last_states: dict = {}


def start() -> Optional[asyncio.Task]:
    if not enabled() or not actors.enabled():
        return None
    log.info("photon alerts on (manager=%s, worker=%s)", bool(_env("MANAGER_PHONE")), bool(_env("WORKER_PHONE")))
    return asyncio.create_task(run())
