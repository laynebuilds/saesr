import asyncio
import json

import httpx

from app import photon_alert
from app.photon_alert import Photon, messages_for


def inc(status, **kw):
    base = {
        "incident_id": "inc-1", "type": "unsafe_lift", "zone_id": "zone-pack-line", "status": status, "severity": 4,
        "keyframes": ["https://x/k0.jpg", "https://x/k1.jpg", "https://x/k2.jpg"], "low_confidence": False, "trace": [],
        "relay": {"triage": {"reason": "Bending at the waist with straight legs."},
                  "coach": {"recommended_fix": "Bend at the knees, keep the back straight."}},
    }
    base.update(kw)
    return {"camera_id": "cam-01", "incidents": [base]}


def test_milestones_go_to_the_right_person_once():
    sent = set()
    first = messages_for(inc("actioned"), sent)
    roles = {(r, k.split(":")[1]) for r, k, _ in first}
    assert roles == {("manager", "confirmed"), ("worker", "coach")}
    manager = next(t for r, _, t in first if r == "manager")
    assert "severity 4/5" in manager and "https://x/k1.jpg" in manager and "Bending at the waist" in manager
    assert "Bend at the knees" in next(t for r, _, t in first if r == "worker")
    sent.update(k for _, k, _ in first)
    assert messages_for(inc("actioned"), sent) == []
    rec = messages_for(inc("recurring"), sent)
    assert [k.split(":")[1] for _, k, _ in rec] == ["recurring"]
    assert rec[0][2].startswith("URGENT, saesr: Worker number 1. Name: ") and "Action: stop the task and retrain now." in rec[0][2]
    low = messages_for(inc("resolved", low_confidence=True), sent)
    assert "low confidence" in low[0][2]


def test_unconfirmed_incidents_stay_quiet():
    assert messages_for(inc("verifying", severity=None), set()) == []
    assert messages_for(inc("dismissed", severity=2), set()) == []


def test_send_finds_the_conversation_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("PHOTON_API_KEY", "k")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "p1")
    posts = []

    def handler(req: httpx.Request):
        if req.method == "GET" and req.url.path.endswith("/users"):
            return httpx.Response(200, json={"items": [{"id": "u1", "kind": "human", "handle": "+1 (415) 555-0100"}, {"id": "line", "kind": "agent", "handle": "+16280000000"}]})
        if req.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "sp", "participantIds": ["line", "u1"]}]})
        posts.append(req)
        return httpx.Response(200, json={})

    p = Photon(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(p.send("+14155550100", "hi", "inc-1:confirmed:manager"))
    body = json.loads(posts[0].content)
    assert posts[0].url.path == "/v1/projects/p1/messages" and posts[0].headers["idempotency-key"] == "inc-1:confirmed:manager"
    assert body == {"spaceId": "sp", "senderId": "line", "content": {"type": "text", "format": "plain", "text": "hi"}}
    assert not asyncio.run(p.send("+447700900000", "hi", "k2"))  # never texted the line


def test_off_without_keys(monkeypatch):
    monkeypatch.delenv("PHOTON_API_KEY", raising=False)
    assert not photon_alert.enabled()


def test_vapi_call_speaks_the_briefing_then_hangs_up(monkeypatch):
    from app import voice_call

    monkeypatch.setenv("VAPI_API_KEY", "vk")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "pn1")
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(201, json={"id": "call_1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = asyncio.run(voice_call.vapi_call("+12485550100", "Worker number 1. Goodbye.", client))
    assert out == "placed call_1"
    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer vk" and body["phoneNumberId"] == "pn1"
    assert body["customer"] == {"number": "+12485550100"} and body["assistant"]["firstMessage"] == "Worker number 1. Goodbye."
    assert {"type": "endCall"} in body["assistant"]["model"]["tools"] and body["assistant"]["maxDurationSeconds"] <= 60


def test_agentphone_call_picks_agent_speaks_briefing_and_hangs_up(monkeypatch):
    from app import voice_call

    monkeypatch.setenv("AGENTPHONE_API_KEY", "sk_live_x")
    monkeypatch.delenv("AGENTPHONE_AGENT_ID", raising=False)
    seen = []
    statuses = iter(["ringing", "in-progress"])

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/agents":
            return httpx.Response(200, json={"data": [{"id": "agt_1"}]})
        if req.method == "POST" and req.url.path == "/v1/calls":
            body = json.loads(req.content)
            assert body["agentId"] == "agt_1" and body["toNumber"] == "+12485550100"
            assert body["initialGreeting"] == "Worker number 1. Goodbye." and body["systemPrompt"]
            return httpx.Response(201, json={"id": "call_9", "status": "queued"})
        if req.method == "GET":
            return httpx.Response(200, json={"id": "call_9", "status": next(statuses, "in-progress")})
        return httpx.Response(200, json={"id": "call_9", "status": "ending"})

    async def scenario():
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
        out = await voice_call.agentphone_call("+12485550100", "Worker number 1. Goodbye.", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        for _ in range(50):
            await _real_sleep(0)
        return out

    assert asyncio.run(scenario()) == "placed call_9"
    assert ("POST", "/v1/calls/call_9/end") in seen


_real_sleep = asyncio.sleep


async def _fast_sleep(_s):
    await _real_sleep(0)


def test_one_channel_per_alert_by_severity(monkeypatch):
    from app.photon_alert import briefing, channel

    monkeypatch.setenv("CALL_MANAGER", "1")
    monkeypatch.delenv("CALL_SEVERITY", raising=False)
    assert channel("manager", "confirmed", 4) == "call"  # high: phone, no text
    assert channel("manager", "confirmed", 5) == "call"
    assert channel("manager", "confirmed", 3) == "text"  # medium and below: text only
    assert channel("manager", "recurring", 4) == "call"
    assert channel("manager", "resolved", 5) == "text"  # outcomes are always a text
    assert channel("worker", "coach", 5) == "text"  # the worker always gets the coaching text
    monkeypatch.setenv("CALL_SEVERITY", "5")
    assert channel("manager", "confirmed", 4) == "text"
    monkeypatch.setenv("CALL_MANAGER", "0")
    assert channel("manager", "confirmed", 5) == "text"  # calling off: everything is a text
    monkeypatch.setenv("WORKER_NAME", "Layne")
    first = briefing({"type": "unsafe_lift", "zone_id": "zone-pack-line", "severity": 4}, repeat=False)
    assert first.startswith("Worker number 1. Name: Layne. Issue: unsafe lift at the pack line, severity 4 of 5")
    assert "Action:" in first
