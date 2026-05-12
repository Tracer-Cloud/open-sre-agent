"""Wrap an :class:`IncidentSink` with correlator-driven dedup & routing.

The :class:`IncidentCorrelator` decides *whether* and *where* an
incident should go; this module adapts that decision into the existing
sink contract (a callable that takes a :class:`HermesIncident`).

Usage::

    correlator = IncidentCorrelator()
    telegram = make_telegram_sink(dispatcher)
    sink = CorrelatingSink(
        correlator=correlator,
        routes={
            RouteDestination.TELEGRAM: telegram,
            RouteDestination.TELEGRAM_WITH_RCA: telegram,
        },
    )
    agent = HermesAgent(..., incident_sink=sink)

Incidents bound for :attr:`RouteDestination.DROP` are counted but
never forwarded. Routes that aren't registered are logged at WARNING
once per (rule, destination) pair so misconfiguration is visible under
typical production log thresholds without flooding the logs.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.hermes.correlator import (
    CorrelatorDecision,
    IncidentCorrelator,
    RouteDestination,
)
from app.hermes.incident import HermesIncident

logger = logging.getLogger(__name__)

IncidentSinkFn = Callable[[HermesIncident], None]

__all__ = ["CorrelatingSink"]


class CorrelatingSink:
    """Sink wrapper that consults a correlator before dispatching."""

    __slots__ = (
        "_correlator",
        "_routes",
        "_default_route",
        "_missing_warned",
        "_lock",
        "_metrics",
    )

    def __init__(
        self,
        *,
        correlator: IncidentCorrelator,
        routes: dict[RouteDestination, IncidentSinkFn],
        default_route: IncidentSinkFn | None = None,
    ) -> None:
        self._correlator = correlator
        self._routes: dict[RouteDestination, IncidentSinkFn] = dict(routes)
        self._default_route = default_route
        self._missing_warned: set[tuple[str, RouteDestination]] = set()
        self._lock = threading.Lock()
        self._metrics: dict[str, int] = {
            "delivered": 0,
            "suppressed": 0,
            "escalated": 0,
            "dropped": 0,
            "unrouted": 0,
            "sink_errors": 0,
        }

    def __call__(self, incident: HermesIncident) -> None:
        decision = self._correlator.correlate(incident)
        if decision.suppressed:
            self._count_suppressed(decision)
            return
        if decision.destination is RouteDestination.DROP:
            self._count_dropped(decision)
            return
        sink_fn = self._routes.get(decision.destination, self._default_route)
        if sink_fn is None:
            self._warn_missing_route(incident.rule, decision.destination)
            self._count_unrouted(decision)
            return
        # Escalated incidents must use a distinct cooldown key so a downstream
        # AlarmDispatcher does not silently suppress them under the same
        # per-fingerprint cooldown window that already fired for the first
        # occurrence. Appending ":escalated" keeps the key stable across
        # repeated escalations for the same underlying event.
        deliver = (
            _with_escalated_fingerprint(decision.deliver)
            if decision.escalated_from is not None
            else decision.deliver
        )
        try:
            sink_fn(deliver)
            # Count as delivered only after the sink call succeeds; a
            # raising sink must not inflate the delivered counter.
            self._count_delivered(decision)
        except Exception:  # noqa: BLE001 — sinks must never crash the agent
            logger.exception(
                "downstream sink raised for incident rule=%s destination=%s",
                incident.rule,
                decision.destination.value,
            )
            with self._lock:
                self._metrics["sink_errors"] += 1

    def close(self) -> None:
        """Close downstream sinks (if supported) and reset correlator state.

        Every downstream sink's ``close()`` is called even if an earlier one
        raises, and ``_correlator.reset()`` always runs in the finally clause
        so :class:`~app.hermes.sinks.TelegramSink` thread-pool workers are
        never leaked by an exception from a sibling sink.
        """
        errors: list[BaseException] = []
        seen: set[int] = set()

        candidates: list[IncidentSinkFn] = list(self._routes.values())
        if self._default_route is not None:
            candidates.append(self._default_route)

        try:
            for sink_fn in candidates:
                key = id(sink_fn)
                if key in seen:
                    continue
                seen.add(key)
                close_fn = getattr(sink_fn, "close", None)
                if not callable(close_fn):
                    continue
                try:
                    close_fn()
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "error closing downstream sink %r during CorrelatingSink.close()",
                        sink_fn,
                    )
                    errors.append(exc)
        finally:
            self._correlator.reset()

        if errors:
            # Re-raise the first exception after all sinks have been given a
            # chance to close.  The correlator is already reset at this point.
            raise errors[0]

    def metrics_snapshot(self) -> dict[str, int]:
        """Return a copy of the running counters. Useful for ops dashboards.

        Keys: ``delivered``, ``suppressed``, ``escalated``, ``dropped``,
        ``unrouted`` (no handler for destination), ``sink_errors`` (downstream
        sink raised).
        """
        with self._lock:
            return dict(self._metrics)

    def _count_suppressed(self, _decision: CorrelatorDecision) -> None:
        # Suppressed implies within_dedup with no escalation (see correlator).
        with self._lock:
            self._metrics["suppressed"] += 1

    def _count_dropped(self, decision: CorrelatorDecision) -> None:
        with self._lock:
            self._metrics["dropped"] += 1
            if decision.escalated_from is not None:
                self._metrics["escalated"] += 1

    def _count_unrouted(self, decision: CorrelatorDecision) -> None:
        with self._lock:
            self._metrics["unrouted"] += 1
            if decision.escalated_from is not None:
                self._metrics["escalated"] += 1

    def _count_delivered(self, decision: CorrelatorDecision) -> None:
        with self._lock:
            self._metrics["delivered"] += 1
            if decision.escalated_from is not None:
                self._metrics["escalated"] += 1

    def _warn_missing_route(self, rule: str, destination: RouteDestination) -> None:
        key = (rule, destination)
        with self._lock:
            if key in self._missing_warned:
                return
            self._missing_warned.add(key)
        logger.warning(
            "no sink registered for destination=%s (rule=%s); dropping",
            destination.value,
            rule,
        )


def _with_escalated_fingerprint(incident: HermesIncident) -> HermesIncident:
    """Return a copy of *incident* whose fingerprint has an ':escalated' suffix.

    This ensures downstream :class:`AlarmDispatcher` instances use a
    distinct cooldown bucket for escalated notifications, preventing the
    first-occurrence cooldown from silently suppressing the escalation alert.
    """
    return HermesIncident(
        rule=incident.rule,
        severity=incident.severity,
        title=incident.title,
        detected_at=incident.detected_at,
        logger=incident.logger,
        fingerprint=f"{incident.fingerprint}:escalated",
        records=incident.records,
        run_id=incident.run_id,
    )
