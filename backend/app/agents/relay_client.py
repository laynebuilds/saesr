"""A small async client for the Agent Relay (Relaycast) workspace API.

Why not the Python SDK: agent-relay-sdk 14.0.0 (communicate mode) wraps relaycast-sdk 1.0.0, whose
pydantic models reject the current relaycast-engine 11.x responses (agent status "active"), so
register_or_rotate and get both fail against a fresh `agent-relay init` server. This client speaks
the same documented HTTP API (the endpoints the SDKs call) and normalizes what we need:

    POST   /v1/agents                       register an agent (workspace key) -> per-agent token
    DELETE /v1/agents/{name}                remove a stale registration (workspace key)
    POST   /v1/channels, /v1/channels/{c}/join
    POST   /v1/channels/{c}/messages        post to a channel (agent token)
    GET    /v1/channels/{c}/messages        read a channel, ?after=<message id>
    POST   /v1/dm                           direct message {to, text}
    GET    /v1/dm/conversations, /v1/dm/{id}/messages

Every message is returned as {"id", "from", "text", "channel", "ts"}, oldest first.
"""
import logging
from typing import Optional

import httpx

log = logging.getLogger("saesr.relay")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per poll would drown the API log


class RelayError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code


class RelayUnavailable(RelayError):
    """Network failure or a 5xx: the caller should back off and retry."""


def _id_key(msg: dict) -> int:
    try:
        return int(msg["id"])
    except (KeyError, ValueError, TypeError):
        return 0


def _norm(raw: dict, channel: Optional[str]) -> dict:
    return {
        "id": str(raw.get("id")),
        "from": raw.get("agent_name") or raw.get("from") or "unknown",
        "text": raw.get("text") or "",
        "channel": channel,
        "ts": raw.get("created_at"),
    }


class _Http:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {token}"}
        )

    async def request(self, method: str, path: str, json: Optional[dict] = None, params: Optional[dict] = None):
        try:
            res = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as err:
            raise RelayUnavailable(0, "network", str(err)) from err
        if res.status_code == 204 or not res.content:
            if res.status_code >= 400:
                raise RelayError(res.status_code, "http", res.reason_phrase)
            return None
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code >= 500:
            raise RelayUnavailable(res.status_code, "server", res.text[:200])
        if res.status_code >= 400 or body.get("ok") is False:
            err = body.get("error") or {}
            raise RelayError(res.status_code, err.get("code", "http"), err.get("message", res.text[:200]))
        return body.get("data", body)

    async def close(self) -> None:
        await self._client.aclose()


class RelayAgent:
    """One agent identity in the workspace, acting with its own token."""

    def __init__(self, name: str, http: _Http) -> None:
        self.name = name
        self._http = http
        self._dm_cursors: dict[str, int] = {}
        self._dm_primed = False

    async def ensure_channel(self, channel: str, topic: Optional[str] = None) -> None:
        try:
            await self._http.request("POST", "/v1/channels", json={"name": channel, "topic": topic})
        except RelayError as err:
            if err.code != "channel_already_exists":
                raise
        await self._http.request("POST", f"/v1/channels/{channel}/join")

    async def post(self, channel: str, text: str) -> dict:
        data = await self._http.request("POST", f"/v1/channels/{channel}/messages", json={"text": text})
        return _norm(data or {}, channel)

    async def dm(self, to: str, text: str) -> dict:
        data = await self._http.request("POST", "/v1/dm", json={"to": to, "text": text})
        msg = (data or {}).get("message") or data or {}
        return _norm({**msg, "agent_name": self.name}, None)

    async def channel_messages(self, channel: str, after: Optional[str] = None, limit: int = 100) -> list[dict]:
        params = {"limit": str(limit)}
        if after:
            params["after"] = after
        data = await self._http.request("GET", f"/v1/channels/{channel}/messages", params=params) or []
        return sorted((_norm(m, channel) for m in data), key=_id_key)

    async def latest_id(self, channel: str) -> Optional[str]:
        newest = await self.channel_messages(channel, limit=1)
        return newest[-1]["id"] if newest else None

    async def inbound_dms(self) -> list[dict]:
        """New DMs sent to this agent since the last call. The first call only primes the cursors."""
        convs = await self._http.request("GET", "/v1/dm/conversations") or []
        fresh: list[dict] = []
        for conv in convs:
            cid = conv.get("id")
            last = _id_key(conv.get("last_message") or {})
            if not self._dm_primed:
                self._dm_cursors[cid] = last
                continue
            seen = self._dm_cursors.get(cid, 0)  # a conversation started after priming is all new
            if last <= seen:
                continue
            msgs = await self._http.request("GET", f"/v1/dm/{cid}/messages", params={"limit": "50"}) or []
            for m in sorted(msgs, key=_id_key):
                if _id_key(m) > seen and m.get("agent_name") != self.name:
                    fresh.append(_norm(m, None))
            self._dm_cursors[cid] = last
        self._dm_primed = True
        return fresh

    async def close(self) -> None:
        await self._http.close()


class RelayClient:
    """Workspace-level client: registers agents and hands out RelayAgent handles."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self._timeout = timeout
        self._admin = _Http(base_url, api_key, timeout)
        self._agents: list[RelayAgent] = []

    async def health(self) -> bool:
        try:
            await self._admin.request("GET", "/health")
            return True
        except RelayError:
            return False

    async def register(self, name: str, kind: str = "agent", persona: Optional[str] = None) -> RelayAgent:
        """Register `name` and act as it. A stale registration from a previous run is replaced."""
        body = {"name": name, "type": kind, "persona": persona}
        try:
            data = await self._admin.request("POST", "/v1/agents", json=body)
        except RelayError as err:
            if err.code != "agent_already_exists":
                raise
            await self._admin.request("DELETE", f"/v1/agents/{name}")
            data = await self._admin.request("POST", "/v1/agents", json=body)
        agent = RelayAgent(name, _Http(self.base_url, data["token"], self._timeout))
        self._agents.append(agent)
        return agent

    async def ensure_exists(self, name: str, kind: str = "human", persona: Optional[str] = None) -> None:
        """Make sure a handle exists without taking it over (a human may hold its token)."""
        try:
            await self._admin.request("POST", "/v1/agents", json={"name": name, "type": kind, "persona": persona})
        except RelayError as err:
            if err.code != "agent_already_exists":
                raise

    async def close(self) -> None:
        for agent in self._agents:
            await agent.close()
        self._agents.clear()
        await self._admin.close()
