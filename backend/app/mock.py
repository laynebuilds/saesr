"""Mock event source so the dashboard can be built before the CV pipeline is live.

Emits realistic incidents on a few cameras and zones at a steady pace, with the
same shape the real pipeline produces.
"""
import asyncio
import itertools
import random
from datetime import datetime, timezone

from .schema import SaesrEvent

CAMERAS = {
    "cam-1-loading-dock": "loading-dock",
    "cam-2-forklift-lane": "forklift-lane",
    "cam-3-packing": "packing",
    "cam-4-cold-store": "cold-store",
}
# type: (weight, severity, detail)
KINDS = {
    "unsafe_lift": (5, "high", "back bent {a} degrees with straight legs while lifting"),
    "no_hardhat": (3, "medium", "person in hard hat zone without a hard hat"),
    "no_vest": (3, "low", "no high-visibility vest detected"),
    "restricted_zone": (2, "high", "person inside the forklift lane while a forklift is moving"),
    "no_hairnet": (1, "medium", "food handling area without a hairnet"),
    "no_gloves": (1, "low", "handling product without gloves"),
}
_counter = itertools.count(1)


def make_event() -> SaesrEvent:
    kind = random.choices(list(KINDS), weights=[k[0] for k in KINDS.values()])[0]
    _, severity, detail = KINDS[kind]
    camera = random.choice(list(CAMERAS))
    now = datetime.now(timezone.utc)
    n = next(_counter)
    eid = f"evt_{now:%Y%m%d_%H%M%S}_{camera.split('-')[1]}_{n:04d}"
    x1, y1 = random.randint(80, 900), random.randint(60, 400)
    return SaesrEvent(
        id=eid,
        camera=camera,
        type=kind,
        severity=severity,
        timestamp=now.isoformat(),
        zone=CAMERAS[camera],
        clip_url=f"/samples/{kind}.mp4",
        frame_url=f"/samples/{kind}.jpg",
        confidence=round(random.uniform(0.62, 0.97), 2),
        bbox=(x1, y1, x1 + random.randint(120, 220), y1 + random.randint(260, 420)),
        detail=detail.format(a=random.randint(48, 78)),
    )


async def run(publish, every: float = 4.0) -> None:
    while True:
        await publish(make_event())
        await asyncio.sleep(random.uniform(every * 0.5, every * 1.5))
