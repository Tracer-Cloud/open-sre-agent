"""Investigation tracking for analytics events."""

from __future__ import annotations

import traceback
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from infrastructure.analytics.investigation_loop import (
    begin_investigation_loop_metrics_scope,
    bound_loop_metrics,
    loop_metrics_from_state,
    merge_loop_properties,
    reset_investigation_loop_metrics,
)
from infrastructure.analytics.provider import Properties
from infrastructure.analytics.source import (
    EntrypointSource,
    TriggerMode,
    build_source_properties,
)

if TYPE_CHECKING:
    from core.agent_harness import SessionCore


_INVESTIGATION_TRACKING_DEPTH: ContextVar[int] = ContextVar(
    "investigation_tracking_depth",
    default=0,
)


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


@contextmanager
def track_investigation(
    *,
    entrypoint: EntrypointSource,
    trigger_mode: TriggerMode,
    input_path: str | None = None,
    input_json: str | None = None,
    interactive: bool = False,
    evaluate_requested: bool = False,
    investigation_id: str | None = None,
    investigation_target: str | None = None,
    session: SessionCore | None = None,
) -> Generator[InvestigationTracker]:
    """Capture investigation lifecycle once, with nested-call dedupe."""
    from infrastructure.analytics.usage_context import bound_usage_context
    from infrastructure.analytics.event_properties import _investigation_started_properties
    from infrastructure.analytics.capture import (
        _capture,
        capture_investigation_completed,
        capture_investigation_failed,
    )
    from infrastructure.analytics.events import Event

    depth = _INVESTIGATION_TRACKING_DEPTH.get()
    token = _INVESTIGATION_TRACKING_DEPTH.set(depth + 1)
    loop_metrics_token = begin_investigation_loop_metrics_scope() if depth == 0 else None
    session_id = str(getattr(session, "session_id", "") or "") or None
    # Bind session for the full lifecycle so nested pipeline work (and callers
    # that did not bind usage context) still stamp session_id explicitly.
    with bound_usage_context(session_id=session_id):
        tracker: InvestigationTracker
        if depth > 0:
            tracker = InvestigationTracker(shared_properties={}, enabled=False)
        else:
            resolved_id = investigation_id or str(uuid4())
            shared_properties = build_source_properties(
                entrypoint=entrypoint,
                trigger_mode=trigger_mode,
                investigation_id=resolved_id,
            )
            if investigation_target:
                shared_properties["investigation_target"] = investigation_target
            if session is not None:
                session.last_investigation_id = resolved_id
            _capture(
                Event.INVESTIGATION_STARTED,
                _investigation_started_properties(
                    input_path=input_path,
                    input_json=input_json,
                    interactive=interactive,
                    evaluate_requested=evaluate_requested,
                    shared_properties=shared_properties,
                ),
            )
            tracker = InvestigationTracker(shared_properties=shared_properties, enabled=True)

        try:
            yielded = tracker
            yield yielded
        except Exception as exc:
            failure_message = str(exc).strip()[:500]
            failure_detail = "".join(traceback.format_exception_only(exc)).strip()[:500]
            capture_investigation_failed(
                tracker=yielded,
                failure_type=type(exc).__name__,
                failure_message=failure_message or type(exc).__name__,
                failure_detail=failure_detail or None,
                investigation_target=investigation_target,
            )
            raise
        else:
            if not yielded.failed and not yielded.completed:
                capture_investigation_completed(tracker=yielded)
        finally:
            _INVESTIGATION_TRACKING_DEPTH.reset(token)
            if depth == 0 and loop_metrics_token is not None:
                reset_investigation_loop_metrics(loop_metrics_token)
