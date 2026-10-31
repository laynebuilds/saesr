"""The supervisor's memory and rules. Pure and synchronous: feed it events and a clock, get findings.

Rules (thresholds in config.Rules):

    zone_hotspot      the same zone keeps producing incidents (N within a window)
    roaming_violator  the same PPE violation on several cameras within a short window; the event
                      contract has no cross-camera person id, so this is the "same person moving
                      across cameras" signal: one worker without a hard hat walking the floor
    shift_spike       too many incidents of any kind within a window for this shift
    unacked_high      a high severity incident nobody acknowledged within ACK_TIMEOUT_S

Windows run on event timestamps, so replayed or delayed events correlate correctly; the ack
timeout runs on the supervisor's clock (when it saw the incident).
"""
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..schema import Event
from .config import PPE_TYPES, Rules, severity_of

SEEN_MAX = 10000
OPEN_MAX = 500


@dataclass
class Finding:
    rule: str
    severity: str
    escalate: bool
    key: str
    summary: str
    event_ids: list[str]
    zone: Optional[str] = None
    cameras: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def event_time(event: Event, fallback: float) -> float:
    try:
        dt = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _mins(seconds: float) -> str:
    return f"{seconds / 60:g} min" if seconds >= 60 else f"{seconds:g} s"


def _counts(events: list[Event], attr: str) -> str:
    return ", ".join(f"{n} {k}" for k, n in Counter(getattr(e, attr) for e in events).most_common())


class Correlator:
    def __init__(self, rules: Rules) -> None:
        self.rules = rules
        self.window: deque[tuple[float, Event]] = deque()
        self.open_high: dict[str, tuple[float, Event]] = {}  # event id -> (seen at, event)
        self.acked: set[str] = set()
        self.escalated: set[str] = set()
        self.last_fired: dict[str, float] = {}
        self.totals: Counter = Counter()
        self.by_zone: Counter = Counter()
        self.by_type: Counter = Counter()
        self.findings_fired: Counter = Counter()
        self.seen_ids: dict[str, None] = {}  # insertion ordered, capped at SEEN_MAX

    # ---- input

    def observe(self, event: Event, now: float) -> list[Finding]:
        if event.event_id in self.seen_ids:
            return []  # the same incident relayed twice is still one incident
        self.seen_ids[event.event_id] = None
        if len(self.seen_ids) > SEEN_MAX:
            self.seen_ids.pop(next(iter(self.seen_ids)))
        t = event_time(event, now)
        self.window.append((t, event))
        self._prune(t)
        self.totals[severity_of(event)] += 1
        self.by_zone[event.zone_id] += 1
        self.by_type[event.type] += 1
        if severity_of(event) == "high":
            self.open_high[event.event_id] = (now, event)
            if len(self.open_high) > OPEN_MAX:
                self.open_high.pop(next(iter(self.open_high)))

        found = [f for f in (self._zone(event, t), self._roaming(event, t), self._spike(t)) if f]
        for f in found:
            self.findings_fired[f.rule] += 1
        return found

    def tick(self, now: float) -> list[Finding]:
        """Time-based rules: high severity incidents still unacknowledged after the timeout."""
        r = self.rules
        overdue = [
            (seen, ev) for eid, (seen, ev) in self.open_high.items()
            if now - seen >= r.ack_timeout_s and eid not in self.escalated
        ]
        if not overdue or now - self.last_fired.get("unacked_high", float("-inf")) < r.unacked_repage_s:
            return []
        self.last_fired["unacked_high"] = now
        overdue.sort(key=lambda p: p[0])
        batch = [ev for _, ev in overdue[: r.unacked_batch_max]]
        self.escalated.update(ev.event_id for ev in batch)
        oldest = int(now - overdue[0][0])
        more = len(overdue) - len(batch)
        summary = (
            f"{len(overdue)} high severity incident{'s' if len(overdue) > 1 else ''} unacknowledged "
            f"for over {_mins(r.ack_timeout_s)} (oldest {oldest} s): {_counts(batch, 'type')} in "
            f"{', '.join(sorted({e.zone_id for e in batch}))}" + (f", plus {more} more" if more > 0 else "")
        )
        self.findings_fired["unacked_high"] += 1
        return [Finding(
            rule="unacked_high", severity="high", escalate=True, key="unacked_high",
            summary=summary, event_ids=[e.event_id for e in batch],
            zone=batch[0].zone_id if len({e.zone_id for e in batch}) == 1 else None,
            cameras=sorted({e.camera_id for e in batch}), types=sorted({e.type for e in batch}),
        )]

    def ack(self, ids: list[str]) -> list[str]:
        """Acknowledge incidents ("all" = every open one). Returns the ids that were open."""
        targets = list(self.open_high) if ids == ["all"] else [i for i in ids if i in self.open_high]
        for eid in targets:
            self.open_high.pop(eid, None)
            self.acked.add(eid)
        return targets

    # ---- rules

    def _recent(self, t: float, span: float) -> list[Event]:
        return [e for ts, e in self.window if t - span <= ts <= t]

    def _cooled(self, key: str, t: float, cooldown: float) -> bool:
        last = self.last_fired.get(key)
        if last is not None and t - last < cooldown:
            return False
        self.last_fired[key] = t
        return True

    def _zone(self, event: Event, t: float) -> Optional[Finding]:
        r = self.rules
        hits = [e for e in self._recent(t, r.zone_repeat_window_s) if e.zone_id == event.zone_id]
        if len(hits) < r.zone_repeat_n or not self._cooled(f"zone_hotspot:{event.zone_id}", t, r.rule_cooldown_s):
            return None
        high = any(severity_of(e) == "high" for e in hits)
        return Finding(
            rule="zone_hotspot", severity="high" if high else "medium", escalate=high,
            key=f"zone_hotspot:{event.zone_id}",
            summary=f"{len(hits)} incidents in {event.zone_id} within {_mins(r.zone_repeat_window_s)} ({_counts(hits, 'type')})",
            event_ids=[e.event_id for e in hits], zone=event.zone_id,
            cameras=sorted({e.camera_id for e in hits}), types=sorted({e.type for e in hits}),
        )

    def _roaming(self, event: Event, t: float) -> Optional[Finding]:
        r = self.rules
        if event.type not in PPE_TYPES:
            return None
        hits = [e for e in self._recent(t, r.roaming_window_s) if e.type == event.type]
        cams = sorted({e.camera_id for e in hits})
        if len(cams) < r.roaming_min_cameras or not self._cooled(f"roaming:{event.type}", t, r.rule_cooldown_s):
            return None
        path = " -> ".join(dict.fromkeys(f"{e.camera_id} ({e.zone_id})" for e in hits))
        return Finding(
            rule="roaming_violator", severity="medium", escalate=True, key=f"roaming:{event.type}",
            summary=f"{event.type} seen on {len(cams)} cameras within {_mins(r.roaming_window_s)}, "
                    f"likely one worker moving across the floor: {path}",
            event_ids=[e.event_id for e in hits], zone=event.zone_id, cameras=cams, types=[event.type],
        )

    def _spike(self, t: float) -> Optional[Finding]:
        r = self.rules
        hits = self._recent(t, r.spike_window_s)
        if len(hits) < r.spike_n or not self._cooled("shift_spike", t, r.spike_cooldown_s):
            return None
        return Finding(
            rule="shift_spike", severity="high", escalate=True, key="shift_spike",
            summary=f"spike: {len(hits)} incidents in {_mins(r.spike_window_s)} (threshold {r.spike_n}); "
                    f"by zone {_counts(hits, 'zone_id')}",
            event_ids=[e.event_id for e in hits][-10:],
            cameras=sorted({e.camera_id for e in hits}), types=sorted({e.type for e in hits}),
        )

    def _prune(self, t: float) -> None:
        horizon = t - self.rules.history_s
        while self.window and self.window[0][0] < horizon:
            self.window.popleft()

    # ---- reporting

    def snapshot(self, now: float, since: float) -> dict:
        """Numbers for the shift brief: the whole shift plus the last brief interval."""
        last = [e for ts, e in self.window if ts >= since]
        return {
            "shift_total": sum(self.totals.values()),
            "shift_by_severity": dict(self.totals),
            "top_zones": self.by_zone.most_common(3),
            "top_types": self.by_type.most_common(3),
            "since_last_brief": len(last),
            "since_last_brief_by_zone": dict(Counter(e.zone_id for e in last)),
            "open_unacked_high": len(self.open_high),
            "oldest_unacked_s": int(now - min(s for s, _ in self.open_high.values())) if self.open_high else 0,
            "acknowledged": len(self.acked),
            "findings": dict(self.findings_fired),
        }
