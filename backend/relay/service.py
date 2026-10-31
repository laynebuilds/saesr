"""HTTP service and the five relay agents. See durable/README.md "Relay protocol"."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx
from fastapi import FastAPI, Header, Request
from pydantic import BaseModel, Field

from app.schema import RelayStart

from . import mailer, policy
from .config import CONFIG
from .llm import LLMUnavailable, Meta, structured

log = logging.getLogger("saesr.relay")
app = FastAPI(title="saesr relay")
_http = httpx.AsyncClient(timeout=10)
_seen: set[str] = set()  # X-Idempotency-Key values already accepted
_tasks: set[asyncio.Task] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def label(t: str) -> str:
    return t.replace("_", " ")


def zone_label(z: str) -> str:
    return z.removeprefix("zone-").replace("-", " ")


# ---- schemas the models must return ---------------------------------------------------------


class Triage(BaseModel):
    confirmed: bool
    severity: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    reason: str


class Coach(BaseModel):
    summary: str
    recommended_fix: str
    standard_tag: Literal["OSHA ergonomics", "OSHA machine guarding", "OSHA PPE", "FSMA", "HACCP", "GMP"]


class ActionPlan(BaseModel):
    kind: Literal["assign_coaching", "notify_floor_display", "open_corrective_record", "flag_batch_hold", "slow_machine"]
    why: str
    audience: Optional[str] = None
    topic: Optional[str] = None
    due: Optional[str] = None
    display: Optional[str] = None
    message: Optional[str] = None
    description: Optional[str] = None
    window: Optional[str] = None
    machine: Optional[str] = None
    target_speed: Optional[str] = None
    duration_s: Optional[int] = None


class FixerPlan(BaseModel):
    actions: list[ActionPlan]


class Verdict(BaseModel):
    summary: str


# ---- backend I/O ----------------------------------------------------------------------------


async def send(camera_id: str, msg: dict) -> None:
    headers = {"x-internal-token": CONFIG.internal_token} if CONFIG.internal_token else {}
    for attempt in range(4):
        try:
            r = await _http.post(f"{CONFIG.backend_url}/internal/cameras/{camera_id}/message", json=msg, headers=headers)
            if r.status_code < 500:
                if r.status_code >= 400:
                    log.warning("actor rejected %s: %s %s", msg.get("kind"), r.status_code, r.text[:200])
                return
        except httpx.HTTPError as err:
            log.warning("send %s failed: %s", msg.get("kind"), err)
        await asyncio.sleep(0.5 * (attempt + 1))  # every ActorMessage is safe to resend


async def incident_state(camera_id: str, incident_id: str) -> Optional[dict]:
    try:
        r = await _http.get(f"{CONFIG.backend_url}/cameras/{camera_id}/state")
        if r.status_code == 200:
            return next((i for i in r.json().get("incidents", []) if i["incident_id"] == incident_id), None)
    except httpx.HTTPError:
        pass
    return None


async def images(urls: list[str]) -> list[dict]:
    """Keyframes as base64 image blocks (the backend serves them locally, so Claude cannot fetch them)."""
    blocks = []
    for u in urls[:3]:
        url = u if u.startswith("http") else CONFIG.backend_url + ("" if u.startswith("/") else "/") + u
        try:
            r = await _http.get(url)
            ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
            if r.status_code == 200 and ctype in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": ctype, "data": base64.b64encode(r.content).decode()}})
        except httpx.HTTPError as err:
            log.warning("keyframe %s: %s", url, err)
    return blocks


# ---- Agent Relay: each step posts what it did to #safety-floor (best effort) -----------------

_relay_agents: dict[str, Any] = {}
_relay_client = None


async def announce(agent: str, text: str) -> None:
    global _relay_client
    try:
        from app.agents import config as relay_config
        from app.agents.relay_client import RelayClient

        cfg = relay_config.load()
        if not cfg.enabled:
            return
        if _relay_client is None:
            _relay_client = RelayClient(cfg.base_url, cfg.api_key)
        if agent not in _relay_agents:
            a = await _relay_client.register(agent, persona=f"saesr relay {agent} agent")
            await a.ensure_channel(cfg.channel, "saesr safety floor")
            _relay_agents[agent] = a
        await _relay_agents[agent].post(cfg.channel, text)
    except Exception as err:  # Relay down never blocks the safety chain
        log.debug("relay announce skipped: %s", err)


# ---- the five agents ------------------------------------------------------------------------


def update(incident_id: str, step: str, status: str, output: dict, started: str, summary: str, meta: Optional[Meta] = None, error: Optional[str] = None) -> dict:
    out = dict(output)
    if meta:
        out.update(meta.usage())
    return {
        "kind": "relay_update",
        "incident_id": incident_id,
        "step": step,
        "status": status,
        "output": out,
        "trace": {"agent": step, "model": meta.model if meta else None, "started_at": started, "ended_at": now(), "output_summary": summary[:300], "error": error},
    }


async def run_triage(s: RelayStart, event: dict) -> None:
    started = now()
    content = await images(s.keyframes or event.get("keyframes") or [])
    content.append(
        {
            "type": "text",
            "text": json.dumps(
                {"type": event["type"], "zone": event["zone_id"], "detector_confidence": event.get("confidence"), "metrics": event.get("metrics", {})}
            ),
        }
    )
    try:
        t, meta = await structured(CONFIG.triage_model, CONFIG.prompt("triage"), content, Triage, timeout_s=10)
        status = "confirmed" if t.confirmed else "dismissed"
        out = {"severity": t.severity if t.confirmed else 1, "confidence": t.confidence, "reason": t.reason}
        await send(s.camera_id, update(s.incident_id, "triage", status, out, started, f"{status}, severity {out['severity']}. {t.reason}", meta))
        await announce("triage", f"TRIAGE {status.upper()} {label(event['type'])} in {zone_label(event['zone_id'])}, severity {out['severity']}: {t.reason}")
        if t.confirmed and t.severity >= 4:  # severity 4 to 5: the supervisor hears about it now
            await mailer.send(
                f"[saesr] Severity {t.severity}: {label(event['type'])} in {zone_label(event['zone_id'])}",
                f"{t.reason}\n\nThe relay is applying the fix now. Incident: {CONFIG.dashboard_url}/incidents/{s.incident_id}",
            )
    except LLMUnavailable as err:
        # Nothing is silently dropped: the incident continues as unverified.
        await send(s.camera_id, update(s.incident_id, "triage", "unverified", {"reason": f"AI triage unavailable: {err}"}, started, "unverified", error=str(err)))
        await announce("triage", f"TRIAGE UNVERIFIED {label(event['type'])} in {zone_label(event['zone_id'])} (AI unavailable)")


async def run_coach(s: RelayStart, event: dict, inc: dict) -> None:
    started = now()
    triage = (inc.get("relay") or {}).get("triage", {})
    facts = {"type": event["type"], "zone": event["zone_id"], "severity": inc.get("severity"), "triage_reason": triage.get("reason")}
    try:
        c, meta = await structured(CONFIG.coach_model, CONFIG.prompt("coach"), [{"type": "text", "text": json.dumps(facts)}], Coach, timeout_s=15)
        out = c.model_dump()
        await send(s.camera_id, update(s.incident_id, "coach", "done", out, started, f"{c.summary} Fix: {c.recommended_fix}", meta))
        await announce("coach", f"COACH {label(event['type'])}: {c.recommended_fix} ({c.standard_tag})")
    except LLMUnavailable as err:
        out = {"summary": f"{label(event['type']).capitalize()} in {zone_label(event['zone_id'])}.", "recommended_fix": "Review the site procedure for this zone with the shift.", "standard_tag": policy.STANDARD.get(event["type"], "")}
        await send(s.camera_id, update(s.incident_id, "coach", "failed", out, started, "coach unavailable, used the default", error=str(err)))


async def run_fixer(s: RelayStart, event: dict, inc: dict) -> None:
    started = now()
    relay = inc.get("relay") or {}
    coach = relay.get("coach", {})
    severity = int(inc.get("severity") or 3)

    if (event.get("metrics") or {}).get("escalation") == "recurring":
        # The fix did not hold: escalate to the supervisor instead of repeating the same actions.
        await mailer.send(
            f"[saesr] Recurring: {label(event['type'])} in {zone_label(event['zone_id'])}",
            f"The same violation was seen again after the fix.\nLast coaching: {coach.get('recommended_fix', '-')}\n\nIncident: {CONFIG.dashboard_url}/incidents/{s.incident_id}",
        )
        await send(s.camera_id, update(s.incident_id, "fixer", "done", {"summary": "Recurring after the fix: escalated to the supervisor by email."}, started, "escalated to supervisor"))
        await announce("fixer", f"ESCALATION recurring {label(event['type'])} in {zone_label(event['zone_id'])}: supervisor emailed")
        return

    kinds = policy.allowed_actions(event["type"], severity)
    plans: dict[str, ActionPlan] = {}
    meta: Optional[Meta] = None
    error = None
    try:
        facts = {"type": event["type"], "zone": event["zone_id"], "severity": severity, "triage_reason": relay.get("triage", {}).get("reason"), "coaching": coach, "actions": kinds}
        plan, meta = await structured(CONFIG.fixer_model, CONFIG.prompt("fixer"), [{"type": "text", "text": json.dumps(facts)}], FixerPlan, timeout_s=15)
        plans = {p.kind: p for p in plan.actions if p.kind in kinds}  # the policy wins: extras are dropped
    except LLMUnavailable as err:
        error = str(err)

    ids = []
    for kind in kinds:
        p = plans.get(kind)
        params = policy.default_params(kind, event, coach)
        if p:
            params.update({k: v for k, v in p.model_dump(exclude={"kind", "why"}).items() if v not in (None, "")})
        if kind == "open_corrective_record":
            params.setdefault("standard", coach.get("standard_tag") or policy.STANDARD.get(event["type"]))
            params["evidence"] = (inc.get("keyframes") or [])[:3] + ([inc["clip_url"]] if inc.get("clip_url") else [])
        params["why"] = p.why if p else "Required by the policy for this hazard."
        aid = policy.action_id(s.incident_id, kind)
        ids.append(aid)
        gated = policy.TIER[kind] == 2
        action = {"action_id": aid, "incident_id": s.incident_id, "kind": kind, "status": "awaiting_approval" if gated else "pending", "params": params}
        await send(s.camera_id, {"kind": "action_update", "incident_id": s.incident_id, "action": action})
        if not gated:
            await execute(s.camera_id, s.incident_id, action)
    summary = f"{len(kinds)} actions: {', '.join(label(k) for k in kinds)}"
    await send(s.camera_id, update(s.incident_id, "fixer", "done" if not error else "failed", {"summary": summary, "actions": ids}, started, summary, meta, error))
    await announce("fixer", f"FIX {label(event['type'])}: {summary}" + (" (slow machine waits for approval)" if "slow_machine" in kinds else ""))


async def execute(camera_id: str, incident_id: str, action: dict) -> None:
    """Run one action. Tier 0 are records; tier 1 and 2 are simulated (logged, labeled in the UI)."""
    kind = action["kind"]
    try:
        if kind == "slow_machine":
            r = await _http.post(f"{CONFIG.backend_url}/mock/machine/slow", json={"incident_id": incident_id, **action.get("params", {})})
            r.raise_for_status()
        else:
            CONFIG.data_dir.mkdir(parents=True, exist_ok=True)
            with open(CONFIG.data_dir / "relay_records.jsonl", "a") as f:
                f.write(json.dumps({"at": now(), "incident_id": incident_id, "kind": kind, "params": action.get("params", {})}) + "\n")
        done = {**action, "status": "done", "executed_at": now()}
    except Exception as err:
        log.warning("action %s failed: %s", kind, err)
        done = {**action, "status": "failed"}
    await send(camera_id, {"kind": "action_update", "incident_id": incident_id, "action": done})


async def run_verifier(s: RelayStart, event: dict) -> None:
    """The actor owns the verification window; wait for its outcome, then write the one-line summary."""
    started = now()
    deadline = asyncio.get_running_loop().time() + CONFIG.verify_wait_s
    inc = None
    while asyncio.get_running_loop().time() < deadline:
        inc = await incident_state(s.camera_id, s.incident_id)
        if inc and inc["status"] in ("resolved", "recurring", "closed", "dismissed"):
            break
        await asyncio.sleep(1)
    status = (inc or {}).get("status", "unknown")
    outcome = {
        "resolved": "low_confidence: the window ended with no repeat and no compliant observation" if (inc or {}).get("low_confidence") else "resolved: a compliant observation followed in the same zone and no repeat was seen",
        "recurring": "recurring: the same violation was seen again in the zone after the fix",
    }.get(status, f"still {status} when the relay stopped waiting")
    meta = None
    try:
        v, meta = await structured(
            CONFIG.verifier_model,
            CONFIG.prompt("verifier"),
            [{"type": "text", "text": json.dumps({"type": event["type"], "zone": event["zone_id"], "outcome": outcome})}],
            Verdict,
            timeout_s=10,
        )
        summary = v.summary
    except LLMUnavailable:
        summary = outcome[0].upper() + outcome[1:] + "."
    await send(s.camera_id, update(s.incident_id, "verifier", "done", {"summary": summary, "outcome": status}, started, summary, meta))
    await announce("verifier", f"VERIFY {label(event['type'])} in {zone_label(event['zone_id'])}: {summary}")


async def run_reporter(s: RelayStart, event: dict, inc: dict) -> None:
    started = now()
    relay = inc.get("relay") or {}
    sev = inc.get("severity") or 3
    lines = [
        f"{label(event['type']).capitalize()} in {zone_label(event['zone_id'])} on {s.camera_id}, severity {sev}.",
        f"Status: {inc.get('status')}.",
        f"Triage: {relay.get('triage', {}).get('reason', '-')}",
        f"Coaching: {relay.get('coach', {}).get('recommended_fix', '-')}",
        "Actions:",
        *[f"  - {label(a['kind'])}: {a['status']}{' (approved by ' + a['approved_by'] + ')' if a.get('approved_by') else ''}" for a in inc.get("actions", [])],
        f"Verification: {relay.get('verifier', {}).get('summary', '-')}",
        "",
        f"Incident: {CONFIG.dashboard_url}/incidents/{s.incident_id}",
    ]
    if inc.get("status") == "resolved":
        outcome = "resolved, no repeat seen (low confidence)" if inc.get("low_confidence") else "fixed and verified"
    else:
        outcome = str(inc.get("status"))
    subject = f"[saesr] {label(event['type']).capitalize()} in {zone_label(event['zone_id'])}: {outcome}"
    if sev <= 3:  # severity 1 to 3: a short line, not a full report
        lines = [lines[0], f"Outcome: {outcome}.", lines[-1]]
    mail = await mailer.send(subject, "\n".join(lines))
    channel = f"email to {CONFIG.supervisor_email}" if mail.get("sent") else "supervisor outbox"
    await send(s.camera_id, update(s.incident_id, "reporter", "done", {"text": subject.removeprefix("[saesr] "), "channel": channel}, started, f"reported to {channel}"))
    await announce("reporter", f"REPORT {subject.removeprefix('[saesr] ')} ({channel})")


async def handle(s: RelayStart) -> None:
    event = s.event.model_dump(mode="json")
    try:
        if s.action is not None:  # an approved gated action: execute it once
            await execute(s.camera_id, s.incident_id, {**s.action.model_dump(mode="json"), "status": "approved"})
            await announce("fixer", f"EXECUTED approved {label(s.action.kind)} for {label(event['type'])} (simulated)")
            return
        if s.step == "triage":
            return await run_triage(s, event)
        if s.step == "verifier":
            return await run_verifier(s, event)
        inc = await incident_state(s.camera_id, s.incident_id) or {}
        await {"coach": run_coach, "fixer": run_fixer, "reporter": run_reporter}[s.step](s, event, inc)
    except Exception:
        log.exception("step %s for %s crashed", s.step, s.incident_id)
        await send(s.camera_id, update(s.incident_id, s.step, "failed", {}, now(), "step crashed", error="relay error"))


@app.post("/relay/start")
async def relay_start(request: Request, x_idempotency_key: Optional[str] = Header(None)):
    body = await request.json()
    start = RelayStart.model_validate(body)
    key = x_idempotency_key or f"{start.incident_id}:{start.step}:{start.action.action_id if start.action else ''}"
    if key in _seen:
        return {"ok": True, "duplicate": True}
    _seen.add(key)
    task = asyncio.create_task(handle(start))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    log.info("accepted %s", key)
    return {"ok": True, "accepted": key}


@app.get("/health")
def health():
    return {"ok": True, "llm": bool(CONFIG.anthropic_key), "email": CONFIG.supervisor_email or "outbox", "models": [CONFIG.triage_model, CONFIG.coach_model]}
