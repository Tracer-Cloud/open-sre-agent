"""Shared record and metric-merge helpers for investigation tracking.

Leaf module: importable from both ``investigation_tracker`` (which owns the
``track_investigation`` context manager) and ``event_properties`` (which reads
tracker fields when building payloads) without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from infrastructure.analytics.investigation_loop import (
    bound_loop_metrics,
    loop_metrics_from_state,
    merge_loop_properties,
)
from infrastructure.analytics.provider import Properties


@dataclass
class InvestigationTracker:
    """Holds shared context for investigation lifecycle captures."""

    shared_properties: Properties
    enabled: bool
    completed: bool = False
    failed: bool = False
    investigation_loop_count: int | None = None
    investigation_iteration_cap: int = MAX_INVESTIGATION_LOOPS

    def record_loop_metrics_from_state(self, state: Mapping[str, object] | None) -> None:
        """Capture canonical loop metrics from the investigation final state."""
        loop_count, iteration_cap = loop_metrics_from_state(state)
        self.investigation_loop_count = loop_count
        self.investigation_iteration_cap = iteration_cap


def _resolve_investigation_loop_metrics(
    *,
    loop_count: int | None = None,
    iteration_cap: int | None = None,
    state: Mapping[str, object] | None = None,
    tracker: InvestigationTracker | None = None,
) -> tuple[int, int]:
    if loop_count is not None:
        resolved_cap = (
            iteration_cap
            if iteration_cap is not None
            else (
                tracker.investigation_iteration_cap
                if tracker is not None
                else MAX_INVESTIGATION_LOOPS
            )
        )
        return max(0, int(loop_count)), max(1, int(resolved_cap))
    bound = bound_loop_metrics()
    if bound is not None:
        return bound
    if state is not None:
        return loop_metrics_from_state(state)
    if tracker is not None and tracker.investigation_loop_count is not None:
        return tracker.investigation_loop_count, tracker.investigation_iteration_cap
    return 0, MAX_INVESTIGATION_LOOPS


def _with_investigation_loop_metrics(
    properties: Properties,
    *,
    loop_count: int | None = None,
    iteration_cap: int | None = None,
    state: Mapping[str, object] | None = None,
    tracker: InvestigationTracker | None = None,
) -> Properties:
    count, cap = _resolve_investigation_loop_metrics(
        loop_count=loop_count,
        iteration_cap=iteration_cap,
        state=state,
        tracker=tracker,
    )
    return merge_loop_properties(properties, loop_count=count, iteration_cap=cap)


__all__ = [
    "InvestigationTracker",
    "_resolve_investigation_loop_metrics",
    "_with_investigation_loop_metrics",
]
