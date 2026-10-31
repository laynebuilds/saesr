"""Send one detection with real frames, for rehearsal and filming:

    cd backend && uv run python -m relay.demo_event missing_hairnet
    cd backend && uv run python -m relay.demo_event unsafe_lift --frames http://localhost:5173/footage/unsafe_lift.jpg

Default frames are the dashboard's stock clips (dashboard/public/footage, served on :5173).
"""
import argparse
import json
import uuid
import urllib.request
from datetime import datetime, timezone

CAMERA = {"unsafe_lift": ("cam-01", "zone-pack-line", "unsafe_lift"), "zone_breach": ("cam-01", "zone-machine-a", "restricted_zone"),
          "missing_hairnet": ("cam-02", "zone-handwash", "no_hairnet"), "missing_gloves": ("cam-02", "zone-handwash", "no_gloves")}

p = argparse.ArgumentParser()
p.add_argument("type", choices=list(CAMERA))
p.add_argument("--backend", default="http://127.0.0.1:8000")
p.add_argument("--footage", default="http://localhost:5173/footage")
p.add_argument("--frames", nargs="*", help="keyframe URLs (default: the stock clip stills)")
p.add_argument("--compliant", type=float, metavar="SECONDS", help="then post a compliant_observation in the same zone after SECONDS (verifies the fix)")
a = p.parse_args()
cam, zone, stem = CAMERA[a.type]
frames = a.frames or [f"{a.footage}/{stem}.jpg", f"{a.footage}/{stem}_after.jpg", f"{a.footage}/{stem}.jpg"]
event = {
    "event_id": str(uuid.uuid4()), "site_id": "site-demo", "camera_id": cam, "type": a.type, "confidence": 0.9,
    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "zone_id": zone,
    "bbox": [0.4, 0.2, 0.2, 0.5], "centroid": {"x": 0.5, "y": 0.45}, "metrics": {}, "keyframes": frames[:3],
    "clip_url": f"{a.footage}/{stem}.mp4",
}
req = urllib.request.Request(f"{a.backend}/events", json.dumps(event).encode(), {"content-type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
if a.compliant is not None:
    import time

    time.sleep(a.compliant)
    ok = dict(event, event_id=str(uuid.uuid4()), type="compliant_observation", timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    req = urllib.request.Request(f"{a.backend}/events", json.dumps(ok).encode(), {"content-type": "application/json"})
    print("compliant_observation:", urllib.request.urlopen(req).read().decode())
