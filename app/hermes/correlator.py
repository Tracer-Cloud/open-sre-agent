"""Incident correlation, deduplication, escalation, and routing.

The classifier emits raw incidents; the correlator decides:

* Have we already paged on this fingerprint recently? (**dedup**)
* Are the same fingerprint or related rules firing repeatedly?
  Escalate severity. (**escalation**)
* Which sink should this incident go to? (**routing**)

Design goals:

* Pure in-memory; no external store. Operators that want durable
  dedup across restarts can subclass and override :meth:`_seen`.
* Thread-safe (single lock guards bucket state). Cheap: the hot path
  is two dict lookups and a deque trim.
* Composable with the existing TelegramSink. The correlator is the
  layer *between* classifier and sink; it doesn't replace either.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.hermes.incident import HermesIncident, IncidentSeverity

DEFAULT_DEDUP_WINDOW_S: Final[float] = 300.0  # 5 minutes
DEFAULT_ESCALATION_WINDOW_S: Final[float] = 600.0  # 10 minutes
DEFAULT_ESCALATION_THRESHOLD: Final[int] = 3
_SEVERITY_ORDER: Final[tuple[IncidentSeverity, ...]] = (
    IncidentSeverity.LOW,
    IncidentSeverity.MEDIUM,
    IncidentSeverity.HIGH,
    IncidentSeverity.CRITICAL,
)


class RouteDestination(StrEnum):
    """Routing targets a correlator may pick for an incident."""

    DROP = "drop"
    TELEGRAM = "telegram"
    TELEGRAM_WITH_RCA = "telegram_with_rca"
    PAGER = "pager"


@dataclass(frozen=True, slots=True)
class CorrelatorDecision:
    """Result of correlating one classifier-emitted incident.

    ``deliver`` is the (possibly mutated) incident the sink should
    publish — severity may have been raised by escalation, and
    ``records`` may have been augmented with prior occurrences.
    ``destination`` tells the dispatcher where to send it.
    ``suppressed`` is True when the incident was deduplicated and the
    dispatcher should drop it; ``deliver`` is still populated so
    callers can log what was suppressed.
    """

    deliver: HermesIncident
    destination: RouteDestination
    suppressed: bool
    repeat_count: int
    escalated_from: IncidentSeverity | None


@dataclass
class _FingerprintState:
    last_seen: datetime
    timestamps: deque[datetime] = field(default_factory=deque)


def default_routing_matrix() -> dict[str, RouteDestination]:
    """Default rule → destination map.

    Rules not present in the matrix fall through to
    :attr:`RouteDestination.TELEGRAM` for HIGH/CRITICAL and
    :attr:`RouteDestination.DROP` for everything else.
    """
    return {
        # Structural rules
        "error_severity": RouteDestination.TELEGRAM_WITH_RCA,
        "traceback": RouteDestination.TELEGRAM_WITH_RCA,
        "warning_burst": RouteDestination.TELEGRAM,
        # Pattern rules
        "oom_killed": RouteDestination.TELEGRAM_WITH_RCA,
        "crash_loop": RouteDestination.PAGER,
        "context_window_exceeded": RouteDestination.TELEGRAM,
        "auth_failure": RouteDestination.TELEGRAM_WITH_RCA,
        "rate_limit": RouteDestination.TELEGRAM,
        "database_wal_growth": RouteDestination.TELEGRAM_WITH_RCA,
        "deadlock": RouteDestination.PAGER,
        "disk_full": RouteDestination.PAGER,
    }


class IncidentCorrelator:
    """Dedup, escalate, and route classifier-emitted incidents."""

    __slots__ = (
        "_dedup_window",
        "_escalation_window",
        "_escalation_threshold",
        "_routing_matrix",
        "_state",
        "_lock",
    )

    def __init__(
        self,
        *,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        escalation_window_s: float = DEFAULT_ESCALATION_WINDOW_S,
        escalation_threshold: int = DEFAULT_ESCALATION_THRESHOLD,
        routing_matrix: dict[str, RouteDestination] | None = None,
    ) -> None:
        if dedup_window_s < 0:
            raise ValueError("dedup_window_s must be >= 0")
        if escalation_window_s <= 0:
            raise ValueError("escalation_window_s must be > 0")
        if escalation_threshold < 2:
            raise ValueError("escalation_threshold must be >= 2")
        self._dedup_window = timedelta(seconds=dedup_window_s)
        self._escalation_window = timedelta(seconds=escalation_window_s)
        self._escalation_threshold = escalation_threshold
        self._routing_matrix = routing_matrix or default_routing_matrix()
        self._state: dict[str, _FingerprintState] = {}
        self._lock = threading.Lock()

    def correlate(self, incident: HermesIncident) -> CorrelatorDecision:
        """Apply dedup, escalation, and routing to a single incident."""
        with self._lock:
            state = self._state.get(incident.fingerprint)
            now = incident.detected_at
            stale_cutoff = now - max(self._dedup_window, self._escalation_window)

            if state is None:
                state = _FingerprintState(
                    last_seen=now,
                    timestamps=deque([now]),
                )
                self._state[incident.fingerprint] = state
                destination = self._route(incident.rule, incident.severity)
                self._evict_stale(stale_cutoff)
                return CorrelatorDecision(
                    deliver=incident,
                    destination=destination,
                    suppressed=False,
                    repeat_count=1,
                    escalated_from=None,
                )

            state.timestamps.append(now)
            self._trim(state.timestamps, now - self._escalation_window)
            repeat_count = len(state.timestamps)

            within_dedup = (now - state.last_seen) < self._dedup_window
            state.last_seen = now

            escalated_from: IncidentSeverity | None = None
            effective_severity = incident.severity
            if repeat_count >= self._escalation_threshold:
                bumped = _bump_severity(effective_severity)
                if bumped is not effective_severity:
                    escalated_from = effective_severity
                    effective_severity = bumped

            delivered = (
                incident
                if effective_severity is incident.severity
                else _with_severity(incident, effective_severity)
            )

            # Escalated incidents always go through, even inside the dedup
            # window — the whole point of escalation is to break through
            # dedup when the same thing keeps happening.
            suppressed = within_dedup and escalated_from is None
            destination = (
                RouteDestination.DROP
                if suppressed
                else self._route(incident.rule, effective_severity)
            )
            self._evict_stale(stale_cutoff)
            return CorrelatorDecision(
                deliver=delivered,
                destination=destination,
                suppressed=suppressed,
                repeat_count=repeat_count,
                escalated_from=escalated_from,
            )

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def _evict_stale(self, cutoff: datetime) -> None:
        """Drop fingerprint rows whose ``last_seen`` predates *cutoff*.

        Called under ``_lock`` after updating the active row so the current
        incident's fingerprint is never evicted in the same call.
        """
        stale = [fp for fp, s in self._state.items() if s.last_seen < cutoff]
        for fp in stale:
            del self._state[fp]

    def _route(self, rule: str, severity: IncidentSeverity) -> RouteDestination:
        explicit = self._routing_matrix.get(rule)
        if explicit is not None:
            # Promote to PAGER when escalation has pushed us to CRITICAL.
            if severity is IncidentSeverity.CRITICAL and explicit is RouteDestination.TELEGRAM:
                return RouteDestination.PAGER
            return explicit
        # Fallback for unknown rules: severity-driven.
        if severity in (IncidentSeverity.HIGH, IncidentSeverity.CRITICAL):
            return RouteDestination.TELEGRAM
        return RouteDestination.DROP

    @staticmethod
    def _trim(timestamps: deque[datetime], cutoff: datetime) -> None:
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()


def correlate_all(
    correlator: IncidentCorrelator,
    incidents: Iterable[HermesIncident],
) -> list[CorrelatorDecision]:
    """Convenience: feed a batch of incidents through *correlator*."""
    return [correlator.correlate(inc) for inc in incidents]


__all__ = [
    "CorrelatorDecision",
    "IncidentCorrelator",
    "RouteDestination",
    "default_routing_matrix",
    "correlate_all",
]


def _bump_severity(current: IncidentSeverity) -> IncidentSeverity:
    try:
        idx = _SEVERITY_ORDER.index(current)
    except ValueError:
        return current
    if idx >= len(_SEVERITY_ORDER) - 1:
        return current
    return _SEVERITY_ORDER[idx + 1]


def _with_severity(incident: HermesIncident, severity: IncidentSeverity) -> HermesIncident:
    return HermesIncident(
        rule=incident.rule,
        severity=severity,
        title=f"[ESCALATED] {incident.title}",
        detected_at=incident.detected_at,
        logger=incident.logger,
        fingerprint=incident.fingerprint,
        records=incident.records,
        run_id=incident.run_id,
    )
