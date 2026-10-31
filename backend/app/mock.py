"""Mock event source (contract v2) so the dashboard and actors can run before the CV pipeline is live.

cam-01 (warehouse) emits unsafe_lift and zone_breach in zone-pack-line / zone-machine-a; cam-02
(food line) emits missing_hairnet and missing_gloves in zone-handwash. Every so often a camera
reports a compliant_observation in a zone it recently flagged, which is what resolves incidents.
Keyframes and clips point at the placeholder media in /samples (named by the old v1 types).
"""
import asyncio
import random
from typing import Optional

from .schema import Event

CAMERAS = ("cam-01", "cam-02")

# type: (camera_id, zones, weight)
KINDS: dict[str, tuple[str, tuple[str, ...], int]] = {
    "unsafe_lift": ("cam-01", ("zone-pack-line",), 4),
    "zone_breach": ("cam-01", ("zone-machine-a", "zone-pack-line"), 3),
    "missing_hairnet": ("cam-02", ("zone-handwash",), 3),
    "missing_gloves": ("cam-02", ("zone-handwash",), 2),
}
COMPLIANT_EVERY = 3  # roughly one compliant observation per this many violations

# v2 type -> sample file stem in /samples (the files predate v2)
SAMPLE_FOR = {
    "unsafe_lift": "unsafe_lift",
    "zone_breach": "restricted_zone",
    "missing_hairnet": "no_hairnet",
    "missing_gloves": "no_gloves",
    "compliant_observation": "no_vest",  # any neutral still works as a placeholder
}


def sample_media(type_: str, base: str = "") -> tuple[list[str], Optional[str]]:
    """Placeholder keyframes (3 URLs) and clip URL for an event type. base = e.g. http://localhost:8000."""
    stem = SAMPLE_FOR.get(type_, "unsafe_lift")
    still = f"{base}/samples/{stem}.jpg"
    return [still, still, still], f"{base}/samples/{stem}.mp4"


def _metrics(type_: str) -> dict:
    if type_ == "unsafe_lift":
        return {"torso_angle_deg": random.randint(48, 78), "duration_s": round(random.uniform(0.8, 2.4), 1)}
    if type_ == "zone_breach":
        return {"dwell_s": round(random.uniform(1.0, 6.0), 1)}
    return {"frames_seen": random.randint(8, 40)}


def make_event(type_: Optional[str] = None, camera_id: Optional[str] = None, zone_id: Optional[str] = None) -> Event:
    """One v2 event. With no arguments, a random violation weighted by KINDS."""
    if type_ is None:
        type_ = random.choices(list(KINDS), weights=[k[2] for k in KINDS.values()])[0]
    if type_ == "compliant_observation":
        camera_id = camera_id or random.choice(CAMERAS)
        zone_id = zone_id or ("zone-handwash" if camera_id == "cam-02" else "zone-pack-line")
    else:
        cam, zones, _ = KINDS[type_]
        camera_id = camera_id or cam
        zone_id = zone_id or random.choice(zones)
    keyframes, clip_url = sample_media(type_)
    x, y = random.uniform(0.2, 0.7), random.uniform(0.2, 0.6)
    w, h = random.uniform(0.1, 0.2), random.uniform(0.25, 0.4)
    return Event(
        camera_id=camera_id,
        type=type_,
        confidence=round(random.uniform(0.62, 0.97), 2),
        zone_id=zone_id,
        bbox=(round(x, 3), round(y, 3), round(w, 3), round(h, 3)),
        centroid={"x": round(x + w / 2, 3), "y": round(y + h / 2, 3)},
        metrics=_metrics(type_),
        keyframes=keyframes,
        clip_url=clip_url,
    )


async def run(publish, every: float = 4.0) -> None:
    recent: list[Event] = []
    n = 0
    while True:
        n += 1
        if recent and n % COMPLIANT_EVERY == 0:
            last = random.choice(recent[-4:])
            event = make_event("compliant_observation", last.camera_id, last.zone_id)
        else:
            event = make_event()
            recent = (recent + [event])[-8:]
        await publish(event)
        await asyncio.sleep(random.uniform(every * 0.5, every * 1.5))
