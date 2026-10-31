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
        if req.method == "GET":
            return httpx.Response(200, json={"items": [{"sentBy": {"handle": "+1 (415) 555-0100"}, "receivedBy": {"id": "line"}, "space": {"id": "sp"}}]})
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
