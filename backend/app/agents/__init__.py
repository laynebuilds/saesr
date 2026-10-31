"""Agent layer: camera agents and a safety supervisor talking over Agent Relay. See README.md.

start() is called from the API lifespan. It returns None (and does nothing) unless Relay is
configured: RELAY_API_KEY (+ RELAY_BASE_URL), or RELAY_LOCAL=1 to use `agent-relay init`'s server.
"""
import asyncio
import logging
from typing import Optional

from . import activity, config
from .runtime import AgentRuntime

log = logging.getLogger("saesr.agents")

current: Optional[AgentRuntime] = None  # the running agent layer, if any


def start() -> Optional[asyncio.Task]:
    global current
    cfg = config.load()
    if not cfg.enabled:
        activity.status.update(enabled=False, connected=False)
        log.info("agents off (set RELAY_LOCAL=1 or RELAY_API_KEY to enable)")
        return None
    current = AgentRuntime(cfg)
    return asyncio.create_task(current.run())


def stop() -> None:
    global current
    current = None
    activity.status.update(connected=False)
