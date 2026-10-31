import os
import sys
import tempfile
from pathlib import Path

# Isolate every test run: its own event log, and a mock producer too slow to add noise.
os.environ["EVENTS_LOG"] = str(Path(tempfile.mkdtemp()) / "events.jsonl")
os.environ["MOCK_EVERY"] = "3600"
os.environ.setdefault("MODE", "mock")
# Never talk to a real Relay or LLM from tests; agent tests inject a fake workspace.
for var in ("RELAY_API_KEY", "RELAY_LOCAL", "OPENAI_API_KEY"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
