"""Event contract (mirror of contract/event.schema.json). Change both together."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

EventType = Literal["unsafe_lift", "no_hardhat", "no_vest", "restricted_zone", "no_hairnet", "no_gloves"]
Severity = Literal["low", "medium", "high"]


class SaesrEvent(BaseModel):
    id: str
    camera: str
    type: EventType
    severity: Severity
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    zone: str
    clip_url: Optional[str] = None
    frame_url: Optional[str] = None
    confidence: Optional[float] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    detail: Optional[str] = None
