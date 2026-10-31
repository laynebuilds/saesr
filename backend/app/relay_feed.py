"""Feed the relay agents' steps (triage, coach, fixer, verifier, reporter) into the dashboard's
agent activity feed (GET /agents/activity, GET /agents/stream).

The relay service reports each step to the camera actor as a trace entry; this task watches the
actor state broadcasts and posts every new trace entry as one agent message, so the Agent Relay
page shows what the agents actually did. It also keeps activity.status pointed at the relay
service, refreshed from its /health every few seconds. Read only: it never writes to the actors.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

from . import actors
from .agents import activity

log = logging.getLogger("saesr.relay_feed")

RELAY_URL = os.environ.get("RELAY_URL", "").rstrip("/")
CHANNEL = "safety-floor"
AGENTS = ["triage", "coach", "fixer", "verifier", "reporter"]
HEALTH_EVERY_S = 5.0
SEEN_MAX = 20000


def trace_items(state: dict, seen: set) -> list[tuple[str, str, Optional[str]]]:
    """New (agent, text, event_id) lines from one CameraState, oldest first. Marks them seen."""
    out = []
    for inc in state.get("incidents") or []:
        label = f"{inc.get('type', '?')} in {inc.get('zone_id', '?')} ({state.get('camera_id', '?')})"
        event_id = (inc.get("event_ids") or [None])[0]
        for t in inc.get("trace") or []:
            key = (inc.get("incident_id"), t.get("agent"), t.get("started_at"))
            if key in seen:
                continue
            seen.add(key)
            text = f"{label}: {t.get('output_summary') or 'done'}"
            if t.get("error"):
                text += f" (error: {t['error']})"
            out.append((t.get("agent") or "agent", text, event_id))
    return out


async def _health(client: httpx.AsyncClient) -> None:
    while True:
        try:
            r = await client.get(f"{RELAY_URL}/health", timeout=3)
            activity.status.update(connected=r.status_code == 200, error=None if r.status_code == 200 else f"health {r.status_code}")
        except httpx.HTTPError as err:
            activity.status.update(connected=False, error=f"relay unreachable: {err.__class__.__name__}")
        await asyncio.sleep(HEALTH_EVERY_S)


async def run() -> None:
    activity.status.update(enabled=True, relay_url=RELAY_URL, channel=CHANNEL, agents=AGENTS, error=None)
    seen: set = set()
    q = actors.open_state_subscription()
    async with httpx.AsyncClient() as client:
        health = asyncio.create_task(_health(client))
        try:
            while True:
                state = await q.get()
                for agent, text, event_id in trace_items(state, seen):
                    activity.record_message(agent, f"#{CHANNEL}", text, event_id=event_id)
                if len(seen) > SEEN_MAX:
                    seen.clear()  # bounded; worst case a few old lines repeat once
        finally:
            health.cancel()
            actors.close_state_subscription(q)


def start() -> Optional[asyncio.Task]:
    """On when the actors bridge is on and RELAY_URL is set, unless the old supervisor owns the feed."""
    if not RELAY_URL or not actors.enabled() or os.environ.get("AGENTS_SUPERVISOR") == "1":
        return None
    log.info("relay feed on: agent steps from the actors -> /agents/activity (relay %s)", RELAY_URL)
    return asyncio.create_task(run())
