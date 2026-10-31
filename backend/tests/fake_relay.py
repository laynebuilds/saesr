"""In-memory stand-in for a Relay workspace with the same surface as app.agents.relay_client."""
import itertools
from typing import Optional

from app.agents.relay_client import RelayError, RelayUnavailable


class FakeWorkspace:
    def __init__(self) -> None:
        self.ids = itertools.count(1000)
        self.agents: dict[str, str] = {}  # name -> kind
        self.channels: dict[str, list[dict]] = {}
        self.members: dict[str, set[str]] = {}
        self.dms: list[dict] = []  # {"id", "from", "to", "text"}
        self.down = False

    def check(self) -> None:
        if self.down:
            raise RelayUnavailable(0, "network", "relay is down")

    def channel_texts(self, channel: str = "safety-floor") -> list[tuple[str, str]]:
        return [(m["from"], m["text"]) for m in self.channels.get(channel, [])]

    def dms_to(self, name: str) -> list[dict]:
        return [d for d in self.dms if d["to"] == name]


class FakeAgent:
    def __init__(self, ws: FakeWorkspace, name: str) -> None:
        self.ws = ws
        self.name = name
        self._dm_cursor = 0
        self._primed = False

    async def ensure_channel(self, channel: str, topic: Optional[str] = None) -> None:
        self.ws.check()
        self.ws.channels.setdefault(channel, [])
        self.ws.members.setdefault(channel, set()).add(self.name)

    async def post(self, channel: str, text: str) -> dict:
        self.ws.check()
        if channel not in self.ws.channels:
            raise RelayError(404, "channel_not_found", channel)
        msg = {"id": str(next(self.ws.ids)), "from": self.name, "text": text, "channel": channel, "ts": None}
        self.ws.channels[channel].append(msg)
        return msg

    async def dm(self, to: str, text: str) -> dict:
        self.ws.check()
        if to not in self.ws.agents:
            raise RelayError(404, "agent_not_found", to)
        msg = {"id": str(next(self.ws.ids)), "from": self.name, "to": to, "text": text, "channel": None, "ts": None}
        self.ws.dms.append(msg)
        return msg

    async def channel_messages(self, channel: str, after: Optional[str] = None, limit: int = 100) -> list[dict]:
        self.ws.check()
        msgs = self.ws.channels.get(channel, [])
        return [m for m in msgs if after is None or int(m["id"]) > int(after)][:limit]

    async def latest_id(self, channel: str) -> Optional[str]:
        msgs = await self.channel_messages(channel)
        return msgs[-1]["id"] if msgs else None

    async def inbound_dms(self) -> list[dict]:
        self.ws.check()
        mine = [d for d in self.ws.dms if d["to"] == self.name and int(d["id"]) > self._dm_cursor]
        if mine:
            self._dm_cursor = int(mine[-1]["id"])
        if not self._primed:
            self._primed = True
            return []
        return mine


class FakeRelay:
    """Factory-compatible with RelayClient(base_url, api_key)."""

    def __init__(self, ws: FakeWorkspace) -> None:
        self.ws = ws
        self.closed = False

    async def register(self, name: str, kind: str = "agent", persona: Optional[str] = None) -> FakeAgent:
        self.ws.check()
        self.ws.agents[name] = kind
        return FakeAgent(self.ws, name)

    async def ensure_exists(self, name: str, kind: str = "human", persona: Optional[str] = None) -> None:
        self.ws.check()
        self.ws.agents.setdefault(name, kind)

    async def close(self) -> None:
        self.closed = True
