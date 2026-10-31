"""Floor manager console: watch the agents talk on Relay, live, and answer the supervisor.

Shows every post in #safety-floor and every DM the supervisor sends to the floor manager.
Whatever you type is sent as a DM to the supervisor: "ack all", "ack evt_...", "status".

    cd backend
    RELAY_LOCAL=1 uv run python scripts/watch_relay.py           # local `agent-relay init` server
    RELAY_LOCAL=1 uv run python scripts/watch_relay.py --raw     # also show the machine lines
    RELAY_LOCAL=1 uv run python scripts/watch_relay.py --as viewer --read-only

By default it signs in as the floor-manager handle (re-registering it, which issues a new token),
so this terminal is where escalations land.
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import config  # noqa: E402
from app.agents.messages import ACTION_PREFIX, EVENT_PREFIX  # noqa: E402
from app.agents.relay_client import RelayClient  # noqa: E402

COLORS = {"supervisor": "\033[1;33m", "camera": "\033[36m", "dm": "\033[1;31m", "me": "\033[32m", "other": "\033[35m"}
RESET = "\033[0m"


def show(kind: str, who: str, where: str, text: str, raw: bool) -> None:
    lines = [ln for ln in text.splitlines() if raw or not ln.startswith((EVENT_PREFIX, ACTION_PREFIX))]
    stamp = datetime.now().strftime("%H:%M:%S")
    color = COLORS.get(kind, "")
    head = f"{color}{stamp} {who} -> {where}{RESET}"
    print(head + "\n    " + "\n    ".join(lines) + "\n", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="name", default=None, help="handle to sign in as (default: floor-manager)")
    ap.add_argument("--raw", action="store_true", help="show the saesr:event / saesr:action machine lines")
    ap.add_argument("--read-only", action="store_true", help="do not read stdin")
    ap.add_argument("--history", type=int, default=10, help="channel messages to show on start")
    args = ap.parse_args()

    cfg = config.load()
    if not cfg.enabled:
        sys.exit("Relay not configured: set RELAY_LOCAL=1 (after `npx -y agent-relay@next init`) or RELAY_API_KEY")
    name = args.name or cfg.floor_manager
    relay = RelayClient(cfg.base_url, cfg.api_key)
    me = await relay.register(name, kind="human", persona="Floor manager console (scripts/watch_relay.py)")
    await me.ensure_channel(cfg.channel)
    await me.inbound_dms()  # prime
    print(f"Signed in to {cfg.base_url} as {name}, watching #{cfg.channel}. Type 'ack all', 'ack evt_...' or 'status'.\n")

    history = await me.channel_messages(cfg.channel, limit=max(1, args.history))
    cursor = history[-1]["id"] if history else None
    for msg in history[-args.history:] if args.history else []:
        kind = "supervisor" if msg["from"] == cfg.supervisor else "camera" if msg["from"].startswith("cam") else "other"
        show(kind, msg["from"], f"#{cfg.channel}", msg["text"], args.raw)

    async def read_stdin() -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            if line.strip():
                await me.dm(cfg.supervisor, line.strip())
                show("me", name, cfg.supervisor, line.strip(), args.raw)

    if not args.read_only:
        asyncio.create_task(read_stdin())
    try:
        while True:
            for msg in await me.channel_messages(cfg.channel, after=cursor):
                cursor = msg["id"]
                if msg["from"] == name:
                    continue
                kind = "supervisor" if msg["from"] == cfg.supervisor else "camera" if msg["from"].startswith("cam") else "other"
                show(kind, msg["from"], f"#{cfg.channel}", msg["text"], args.raw)
            for msg in await me.inbound_dms():
                show("dm", msg["from"], f"DM {name}", msg["text"], args.raw)
            await asyncio.sleep(0.5)
    finally:
        await relay.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
