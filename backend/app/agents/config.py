"""Every knob of the agent layer in one place: Relay connection, agent names, rule thresholds.

All values can be overridden by env vars of the same name (upper case), so the demo can be
tuned without code changes, e.g. ACK_TIMEOUT_S=15 ZONE_REPEAT_N=4.
"""
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

LOCAL_RELAY_DIR = Path.home() / ".agentworkforce" / "relay"
PPE_TYPES = ("no_hardhat", "no_vest", "no_hairnet", "no_gloves")  # violations that travel with the person


@dataclass(frozen=True)
class Rules:
    # zone_hotspot: ZONE_REPEAT_N incidents in one zone within ZONE_REPEAT_WINDOW_S
    zone_repeat_n: int = 3
    zone_repeat_window_s: float = 120.0
    # roaming_violator: the same PPE violation seen on 2+ cameras within ROAMING_WINDOW_S,
    # most likely one worker walking the floor without the gear
    roaming_window_s: float = 90.0
    roaming_min_cameras: int = 2
    # shift_spike: SPIKE_N incidents of any kind within SPIKE_WINDOW_S
    spike_n: int = 8
    spike_window_s: float = 60.0
    # unacked_high: a high severity incident nobody acknowledged within ACK_TIMEOUT_S
    ack_timeout_s: float = 30.0
    unacked_batch_max: int = 5  # one escalation DM lists at most this many overdue incidents
    unacked_repage_s: float = 30.0  # at most one unacked page per this many seconds; overdue ones batch up
    # the same finding (rule + zone/type) is not repeated within RULE_COOLDOWN_S
    rule_cooldown_s: float = 90.0
    spike_cooldown_s: float = 180.0
    # rolling shift brief posted to the channel
    brief_every_s: float = 90.0
    # how much history the correlator keeps
    history_s: float = 900.0


@dataclass(frozen=True)
class AgentsConfig:
    base_url: str = "http://127.0.0.1:8787"
    api_key: Optional[str] = None  # Relay workspace key (rk_live_... locally, ar_ws_... hosted)
    channel: str = "safety-floor"
    supervisor: str = "safety-supervisor"
    floor_manager: str = "floor-manager"
    dashboard: str = "saesr-dashboard"  # identity used when an ack comes from the dashboard API
    poll_s: float = 0.5
    openai_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 8.0
    rules: Rules = field(default_factory=Rules)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


def _local_relay() -> tuple[Optional[str], Optional[str]]:
    """Read the workspace key and URL that `npx agent-relay@next init` saved on this machine."""
    try:
        ws = json.loads((LOCAL_RELAY_DIR / "workspaces.json").read_text())
        key = ws["workspaces"][ws.get("active", "local")]["key"]
    except (OSError, KeyError, ValueError):
        return None, None
    try:
        url = json.loads((LOCAL_RELAY_DIR / "local" / "server.json").read_text())["origin"]
    except (OSError, KeyError, ValueError):
        url = None
    return key, url


def _coerce(raw: str, current):
    return type(current)(raw)


def load() -> AgentsConfig:
    """Agents are enabled when RELAY_API_KEY is set, or RELAY_LOCAL=1 and a local Relay exists."""
    env = os.environ
    key = env.get("RELAY_API_KEY") or None
    url = env.get("RELAY_BASE_URL") or None
    if not key and env.get("RELAY_LOCAL", "").lower() in ("1", "true", "yes"):
        key, local_url = _local_relay()
        url = url or local_url
    rules = Rules()
    overrides = {f.name: _coerce(env[f.name.upper()], getattr(rules, f.name)) for f in fields(Rules) if f.name.upper() in env}
    base = AgentsConfig()
    return AgentsConfig(
        base_url=(url or base.base_url).rstrip("/"),
        api_key=key,
        channel=env.get("RELAY_CHANNEL", base.channel).lstrip("#"),
        supervisor=env.get("RELAY_SUPERVISOR", base.supervisor),
        floor_manager=env.get("RELAY_FLOOR_MANAGER", base.floor_manager),
        poll_s=float(env.get("RELAY_POLL_S", base.poll_s)),
        openai_model=env.get("OPENAI_MODEL", base.openai_model),
        rules=Rules(**{**rules.__dict__, **overrides}),
    )
