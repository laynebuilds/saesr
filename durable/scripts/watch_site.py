"""Print every SiteState the Site actor broadcasts. The same stream a dashboard socket receives.

    cd durable && uv run python scripts/watch_site.py [site-id] [max-messages]
"""
import json
import sys

from durable_actors import Client
from websockets.sync.client import connect

site_id = sys.argv[1] if len(sys.argv) > 1 else "site-demo"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

grant = Client().prepare_websocket("Site", site_id, {"name": "watch_site.py"})
with connect(grant.websocket_url) as ws:
    n = 0
    while True:
        s = json.loads(ws.recv())
        n += 1
        cams = {c["camera_id"]: (c["status"], c["version"], c["compliance_score"]) for c in s["cameras"]}
        print(f"score={s['site_score']} active={s['active_incidents']} fixes={s['autonomous_fixes']} cameras={cams}", flush=True)
        if limit and n >= limit:
            break
