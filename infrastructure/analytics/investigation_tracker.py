"""Investigation tracking for analytics events."""

from __future__ import annotations

import traceback
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import uuid4

from infrastructure.analytics.investigation_loop import (
    begin_investigation_loop_metrics_scope,
    reset_investigation_loop_metrics,
)
from infrastructure.analytics.investigation_tracker_types import InvestigationTracker
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
    from infrastructure.analytics.capture import (
        _capture,
        capture_investigation_completed,
        capture_investigation_failed,
    )
    from infrastructure.analytics.event_properties import _investigation_started_properties
    from infrastructure.analytics.events import Event
    from infrastructure.analytics.usage_context import bound_usage_context

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


__all__ = ["track_investigation"]
