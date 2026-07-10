"""Canonical investigation loop metrics for analytics events.

Convention: ``investigation_loop_count`` is the number of completed LLM ReAct
iterations in the gather-evidence agent (0 before the first invoke).
Seed tool calls before the loop are not counted.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from platform.analytics.provider import Properties

_loop_metrics: ContextVar[tuple[int, int] | None] = ContextVar(
    "investigation_loop_metrics",
    default=None,
)


def investigation_loop_count_from_state(state: Mapping[str, Any] | None) -> int:
    """Read the canonical loop counter from investigation state."""
    if state is None:
        return 0
    raw = state.get("investigation_loop_count")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int | float):
        return max(0, int(raw))
    return 0


def investigation_iteration_cap_from_state(state: Mapping[str, Any] | None) -> int:
    """Read the configured iteration cap from state, else the global default."""
    if state is None:
        return MAX_INVESTIGATION_LOOPS
    raw = state.get("investigation_iteration_cap")
    if isinstance(raw, bool):
        return MAX_INVESTIGATION_LOOPS
    if isinstance(raw, int | float) and int(raw) > 0:
        return int(raw)
    return MAX_INVESTIGATION_LOOPS


def loop_metrics_from_state(
    state: Mapping[str, Any] | None,
) -> tuple[int, int]:
    """Return ``(loop_count, iteration_cap)`` from investigation state."""
    return (
        investigation_loop_count_from_state(state),
        investigation_iteration_cap_from_state(state),
    )


def loop_properties(
    *,
    loop_count: int,
    iteration_cap: int,
) -> Properties:
    """Build required loop metric properties for PostHog events."""
    return {
        "investigation_loop_count": max(0, int(loop_count)),
        "investigation_iteration_cap": max(1, int(iteration_cap)),
    }


def merge_loop_properties(
    properties: Properties,
    *,
    loop_count: int,
    iteration_cap: int,
) -> Properties:
    """Attach loop metrics to an existing analytics property dict."""
    return {**properties, **loop_properties(loop_count=loop_count, iteration_cap=iteration_cap)}


def begin_investigation_loop_metrics_scope() -> Token[tuple[int, int] | None]:
    """Start a scoped investigation loop-metrics context."""
    return _loop_metrics.set(None)


def bind_investigation_loop_metrics_from_state(state: Mapping[str, Any] | None) -> None:
    """Publish loop metrics for the active investigation tracking context."""
    count, cap = loop_metrics_from_state(state)
    bind_investigation_loop_metrics(loop_count=count, iteration_cap=cap)


def bind_investigation_loop_metrics(*, loop_count: int, iteration_cap: int) -> None:
    """Publish explicit loop metrics for the active investigation tracking context."""
    _loop_metrics.set((max(0, int(loop_count)), max(1, int(iteration_cap))))


def publish_loop_metrics_from_stream_failure(exc: BaseException) -> BaseException:
    """Bind loop metrics on this thread when *exc* carries them, then unwrap."""
    loop_count = getattr(exc, "loop_count", None)
    iteration_cap = getattr(exc, "iteration_cap", None)
    cause = getattr(exc, "cause", None)
    if (
        cause is not None
        and isinstance(cause, BaseException)
        and loop_count is not None
        and iteration_cap is not None
        and not isinstance(loop_count, bool)
        and not isinstance(iteration_cap, bool)
    ):
        bind_investigation_loop_metrics(
            loop_count=int(loop_count),
            iteration_cap=int(iteration_cap),
        )
        return cause
    return exc


def reset_investigation_loop_metrics(token: Token[tuple[int, int] | None]) -> None:
    """Restore loop metrics from ``begin_investigation_loop_metrics_scope``."""
    _loop_metrics.reset(token)


def bound_loop_metrics() -> tuple[int, int] | None:
    """Return bound loop metrics for the current context, if any."""
    return _loop_metrics.get()


__all__ = [
    "begin_investigation_loop_metrics_scope",
    "bind_investigation_loop_metrics",
    "bind_investigation_loop_metrics_from_state",
    "bound_loop_metrics",
    "investigation_iteration_cap_from_state",
    "investigation_loop_count_from_state",
    "loop_metrics_from_state",
    "loop_properties",
    "merge_loop_properties",
    "publish_loop_metrics_from_stream_failure",
    "reset_investigation_loop_metrics",
]
