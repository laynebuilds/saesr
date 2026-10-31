import os
import sys
import tempfile
from pathlib import Path

# Isolate every test run: its own event log, and a mock producer too slow to add noise.
os.environ["EVENTS_LOG"] = str(Path(tempfile.mkdtemp()) / "events.jsonl")
os.environ["MOCK_EVERY"] = "3600"
os.environ.setdefault("MODE", "mock")
# Never talk to a real Relay or LLM from tests; agent tests inject a fake workspace.
# Nor to real actors, Supabase or the old supervisor; route tests fake app.actors.
for var in ("RELAY_API_KEY", "RELAY_LOCAL", "OPENAI_API_KEY", "ACTORS_URL", "DURABLE_ACTORS_CONTROL_PLANE_URL",
            "SUPABASE_URL", "SUPABASE_SECRET_KEY", "AGENTS_SUPERVISOR", "ADMIN_TOKEN", "INTERNAL_TOKEN", "RELAY_URL"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# app/actors.py is owned by the actors work stream. Until its v2 version lands, the old module does
# not import against schema v2; fall back to a disabled stub with the agreed interface so the API
# imports. Route tests monkeypatch these functions with fakes either way.
try:
    import app.actors  # noqa: F401
except ImportError:
    import asyncio
    import types

    stub = types.ModuleType("app.actors")
    stub.STUB = True
    stub.enabled = lambda: False
    stub.start = lambda cameras: None
    stub.stop = lambda: None
    stub.status = lambda: {"enabled": False, "stub": True}

    async def _none(*a, **k):
        return None

    async def _send(camera_id, msg):
        return {"ok": False, "error": "actors stub"}

    async def _grant(kind, actor_id):
        return {}

    stub.send = _send
    stub.camera_state = _none
    stub.site_state = _none
    stub.socket_grant = _grant
    stub.open_state_subscription = lambda: asyncio.Queue()
    stub.close_state_subscription = lambda q: None
    sys.modules["app.actors"] = stub
    import app

    app.actors = stub
