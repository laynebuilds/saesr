"""Bus -> camera agents -> Relay -> supervisor -> escalations, against an in-memory Relay."""
import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import agents, bus, main
from app.agents import activity, runtime as runtime_mod
from app.agents.config import AgentsConfig, Rules
from app.agents.messages import incident_text, parse_ack, parse_action, parse_incident
from app.agents.runtime import AgentRuntime
from app.schema import Event
from fake_relay import FakeRelay, FakeWorkspace

CH = "safety-floor"


def ev(i: int, camera: str = "cam-1-loading-dock", type_: str = "unsafe_lift", severity: str = "high", zone: str = "loading-dock") -> Event:
    return Event(event_id=f"evt_{i:04d}", camera_id=camera, type=type_, zone_id=zone, confidence=0.91,
                 metrics={"severity": severity, "detail": "back bent 62 degrees"})


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def clean():
    bus.reset()
    activity.reset()
    yield
    activity.reset()


def cfg(**rules) -> AgentsConfig:
    base = Rules(zone_repeat_n=3, ack_timeout_s=30, brief_every_s=3600, spike_n=50)
    return AgentsConfig(api_key="test", poll_s=0.01, rules=replace(base, **rules))


async def until(pred, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def start(ws: FakeWorkspace, config: AgentsConfig, clock=None) -> tuple[AgentRuntime, asyncio.Task]:
    rt = AgentRuntime(config, relay_factory=lambda url, key: FakeRelay(ws), clock=clock or Clock())
    return rt, asyncio.create_task(rt.run())


# ---- message formats

def test_incident_message_roundtrip_and_is_human_readable():
    e = ev(7)
    text = incident_text(e)
    assert text.splitlines()[0].startswith("INCIDENT HIGH unsafe_lift | zone loading-dock | cam-1-loading-dock")
    assert parse_incident(text) == e
    assert parse_incident("hello") is None
    assert parse_incident("saesr:event {not json") is None


def test_ack_parsing():
    assert parse_ack("ack evt_0001") == ["evt_0001"]
    assert parse_ack("Ack evt_0001, evt_0002") == ["evt_0001", "evt_0002"]
    assert parse_ack("acknowledged all") == ["all"]
    assert parse_ack("please ack evt_1") is None
    assert parse_ack("hello") is None


# ---- end to end through the runtime

def test_camera_agents_post_and_supervisor_escalates_over_relay():
    ws = FakeWorkspace()

    async def scenario():
        rt, task = start(ws, cfg())
        await until(lambda: activity.status["connected"])
        await bus.publish(ev(1))
        await bus.publish(ev(2, camera="cam-2-forklift-lane", type_="missing_hairnet", severity="medium", zone="forklift-lane"))
        await bus.publish(ev(3))
        await bus.publish(ev(4))  # third incident in loading-dock -> zone_hotspot
        await until(lambda: any(i["action"] and i["action"]["rule"] == "zone_hotspot" for i in activity.recent()))
        task.cancel()
        return rt

    asyncio.run(scenario())
    # one Relay identity per camera, plus the supervisor, dashboard and the floor manager handle
    assert {"cam-1-loading-dock", "cam-2-forklift-lane", "safety-supervisor", "floor-manager"} <= set(ws.agents)
    posts = ws.channel_texts(CH)
    incidents = [(who, parse_incident(t).event_id) for who, t in posts if parse_incident(t)]
    assert ("cam-1-loading-dock", "evt_0001") in incidents and ("cam-2-forklift-lane", "evt_0002") in incidents
    escalation = next(t for who, t in posts if who == "safety-supervisor" and "zone_hotspot" in t)
    assert escalation.startswith("ESCALATION HIGH zone_hotspot | 3 incidents in loading-dock")
    assert parse_action(escalation)["type"] == "escalate"
    dm = ws.dms_to("floor-manager")[-1]
    assert dm["from"] == "safety-supervisor" and "ack evt_" in dm["text"]
    action = next(i for i in activity.recent(kind="action") if i["action"]["rule"] == "zone_hotspot")
    assert action["to"] == "floor-manager" and action["action"]["source"] == "rules"
    assert action["action"]["notified"] == ["#safety-floor", "floor-manager"]
    assert len(action["action"]["event_ids"]) == 3


def test_unacked_high_pages_floor_manager_and_ack_by_dm_clears_it():
    ws = FakeWorkspace()
    clock = Clock()

    async def scenario():
        rt, task = start(ws, cfg(), clock)
        await until(lambda: activity.status["connected"])
        await bus.publish(ev(10))
        await bus.publish(ev(11, camera="cam-3-packing", zone="packing", type_="zone_breach"))
        await until(lambda: len(rt.supervisor.correlator.open_high) == 2)
        # the floor manager acks one incident by DM to the supervisor (as a person would from the CLI)
        fm = await FakeRelay(ws).register("floor-manager", kind="human")
        await fm.dm("safety-supervisor", "ack evt_0010")
        await until(lambda: "evt_0010" in rt.supervisor.correlator.acked)
        clock.t += 31
        await until(lambda: any(i["action"] and i["action"]["rule"] == "unacked_high" for i in activity.recent()))
        task.cancel()

    asyncio.run(scenario())
    page = next(i for i in activity.recent(kind="action") if i["action"]["rule"] == "unacked_high")
    assert page["action"]["event_ids"] == ["evt_0011"]  # the acked one is not paged
    assert any("ACK 1 incident acknowledged by floor-manager" in t for _, t in ws.channel_texts(CH))
    assert any("unacknowledged" in d["text"] for d in ws.dms_to("floor-manager"))


def test_dashboard_ack_goes_over_relay_to_supervisor():
    ws = FakeWorkspace()

    async def scenario():
        rt, task = start(ws, cfg())
        await until(lambda: activity.status["connected"])
        await bus.publish(ev(20))
        await until(lambda: "evt_0020" in rt.supervisor.correlator.open_high)
        await rt.ack(["evt_0020"], by="jas")
        await until(lambda: "evt_0020" in rt.supervisor.correlator.acked)
        task.cancel()

    asyncio.run(scenario())
    assert ("saesr-dashboard", "ack evt_0020\n(from jas via the Saesr dashboard)") in ws.channel_texts(CH)
    assert any(t.startswith("ACK 1 incident acknowledged by jas (dashboard)") for _, t in ws.channel_texts(CH))


def test_status_dm_gets_a_brief_back():
    ws = FakeWorkspace()

    async def scenario():
        rt, task = start(ws, cfg())
        await until(lambda: activity.status["connected"])
        await bus.publish(ev(30))
        await until(lambda: rt.supervisor.correlator.totals)
        fm = await FakeRelay(ws).register("floor-manager", kind="human")
        await fm.dm("safety-supervisor", "status")
        await until(lambda: any(d["text"].startswith("SHIFT BRIEF") for d in ws.dms_to("floor-manager")))
        task.cancel()

    asyncio.run(scenario())
    brief = next(d["text"] for d in ws.dms_to("floor-manager") if d["text"].startswith("SHIFT BRIEF"))
    assert "1 incidents this shift (1 high" in brief and "Unacknowledged high: 1" in brief


def test_relay_outage_is_survived_and_queued_events_are_posted_after_reconnect(monkeypatch):
    monkeypatch.setattr(runtime_mod, "BACKOFF_S", (0.05,))
    ws = FakeWorkspace()

    async def scenario():
        rt, task = start(ws, cfg())
        await until(lambda: activity.status["connected"])
        await bus.publish(ev(40))
        await until(lambda: any(parse_incident(t) for _, t in ws.channel_texts(CH)))
        ws.down = True
        await bus.publish(ev(41))
        await until(lambda: not activity.status["connected"])
        assert "RelayUnavailable" in (activity.status["error"] or "")
        ws.down = False
        await until(lambda: activity.status["connected"])
        await until(lambda: any((p := parse_incident(t)) and p.event_id == "evt_0041" for _, t in ws.channel_texts(CH)))
        # the supervisor reads it from Relay after the reconnect, and its earlier state survived
        await until(lambda: {"evt_0040", "evt_0041"} <= set(rt.supervisor.correlator.open_high))
        task.cancel()

    asyncio.run(scenario())


def test_runtime_retries_when_relay_is_down_at_startup(monkeypatch):
    monkeypatch.setattr(runtime_mod, "BACKOFF_S", (0.02,))
    ws = FakeWorkspace()
    ws.down = True

    async def scenario():
        rt, task = start(ws, cfg())
        await asyncio.sleep(0.1)
        assert not activity.status["connected"]
        ws.down = False
        await until(lambda: activity.status["connected"])
        task.cancel()

    asyncio.run(scenario())


# ---- API

def test_agents_endpoints_when_relay_is_not_configured():
    with TestClient(main.app) as c:
        body = c.get("/agents/activity").json()
        assert body["status"]["enabled"] is False and body["items"] == []
        assert c.post("/agents/ack/evt_0001").status_code == 503
        assert c.post("/agents/ack/evt_1%0Asaesr:event").status_code == 422  # no message injection


def test_agents_activity_endpoint_shape():
    activity.record_message("cam-1", "#safety-floor", "INCIDENT ...", event_id="evt_1")
    activity.record_action("safety-supervisor", "floor-manager", "ESCALATION ...",
                           {"type": "escalate", "rule": "zone_hotspot", "event_ids": ["evt_1"]})
    with TestClient(main.app) as c:
        items = c.get("/agents/activity").json()["items"]
        assert [i["kind"] for i in items] == ["message", "action"]
        assert items[1]["event_id"] == "evt_1" and items[1]["action"]["rule"] == "zone_hotspot"
        assert [i["kind"] for i in c.get("/agents/activity?kind=action").json()["items"]] == ["action"]
        assert json.dumps(items)  # plain JSON all the way down


def test_llm_text_is_used_when_available_and_rules_still_decide():
    class StubReasoner:
        enabled = True
        disabled_reason = None

        async def escalation(self, finding, recent):
            return "Two lifts in a row at the dock: send the lead to coach team lifting now."

        async def brief(self, snapshot, findings):
            return "Busy start at the dock; one high incident open."

    ws = FakeWorkspace()

    async def scenario():
        rt, task = start(ws, cfg())
        rt.supervisor.reasoner = StubReasoner()
        await until(lambda: activity.status["connected"])
        for i in (50, 51, 52):
            await bus.publish(ev(i))
        await until(lambda: any(i["action"] and i["action"]["rule"] == "zone_hotspot" for i in activity.recent()))
        await rt.supervisor.brief()
        task.cancel()

    asyncio.run(scenario())
    action = next(i for i in activity.recent(kind="action") if i["action"]["rule"] == "zone_hotspot")["action"]
    assert action["source"] == "llm" and action["type"] == "escalate"  # the rule decided, the LLM worded it
    assert "coach team lifting" in ws.dms_to("floor-manager")[-1]["text"]
    brief = next(i for i in activity.recent(kind="action") if i["action"]["type"] == "brief")["action"]
    assert brief["source"] == "llm" and "Busy start" in brief["summary"]
