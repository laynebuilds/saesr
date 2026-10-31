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
            add("manager", "recurring", f"saesr escalation: {what} happened again after the fix. The fix did not hold; a work order has been raised.")
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
        """(spaceId, senderId) for a phone that has texted the line; cached."""
        want = normalize(phone)
        if want in self.convos:
            return self.convos[want]
        data = await self._req("GET", "/messages?direction=received&order=desc&pageSize=100")
        for m in data.get("items") or []:
            if normalize((m.get("sentBy") or {}).get("handle", "")) == want and m.get("receivedBy"):
                self.convos[want] = (m["space"]["id"], m["receivedBy"]["id"])
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
            if state.get("camera_id") not in primed:
                # first state after a (re)start: what already happened is history, only new milestones alert
                primed.add(state.get("camera_id"))
                sent.update(k for _, k, _ in messages_for(state, sent))
                continue
            for role, key, text in messages_for(state, sent):
                if not phones.get(role):
                    sent.add(key)
                    continue
                try:
                    if await photon.send(phones[role], text, key):
                        sent.add(key)
                        log.info("photon: sent %s", key)
                except (httpx.HTTPError, KeyError, ValueError) as err:
                    log.warning("photon: send %s failed (%s); will retry on the next update", key, err)
    finally:
        actors.close_state_subscription(q)


def start() -> Optional[asyncio.Task]:
    if not enabled() or not actors.enabled():
        return None
    log.info("photon alerts on (manager=%s, worker=%s)", bool(_env("MANAGER_PHONE")), bool(_env("WORKER_PHONE")))
    return asyncio.create_task(run())
