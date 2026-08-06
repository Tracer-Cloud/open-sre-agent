"""Per-turn circuit breaker over tool sources that fail to connect.

Installed as :class:`~core.execution.ToolExecutionHooks` on a bounded tool
loop: the first transport-level failure (connect timeout, connection refused)
marks that tool's source unreachable for the rest of the run, and later calls
to the same source are blocked immediately with a reason that steers the model
to healthy sources instead of re-paying the connect timeout.
"""

from __future__ import annotations

import threading

from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)

# Transport-level failure signatures (host unreachable). Application errors
# from a reachable service — "datasource not found", auth failures, empty
# results — must NOT trip the breaker: the source can still answer other calls.
_CONNECTIVITY_ERROR_MARKERS = (
    "connection refused",
    "connect timeout",
    "connecttimeouterror",
    "connection timed out",
    "max retries exceeded",
    "name or service not known",
    "temporary failure in name resolution",
    "no route to host",
    "network is unreachable",
)

# Sources that cannot be meaningfully marked down as a unit.
_UNBREAKABLE_SOURCES = frozenset({"", "unknown"})

_REASON_SUMMARY_CHARS = 160


def _error_text(result: ToolExecutionResult) -> str:
    if isinstance(result.content, str):
        return result.content
    return str(result.details if result.details is not None else result.content)


def _is_connectivity_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(marker in lowered for marker in _CONNECTIVITY_ERROR_MARKERS)


def _summarize(error_text: str) -> str:
    single_line = " ".join(error_text.split())
    if len(single_line) <= _REASON_SUMMARY_CHARS:
        return single_line
    return single_line[:_REASON_SUMMARY_CHARS] + "…"


class SourceCircuitBreaker:
    """Tracks sources that failed at the transport level within one run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._down: dict[str, str] = {}

    def hooks(self) -> ToolExecutionHooks:
        """Return execution hooks enforcing the breaker on a tool loop."""
        return ToolExecutionHooks(
            before_tool_call=self._skip_if_source_down,
            after_tool_call=self._mark_source_on_connectivity_error,
        )

    def _skip_if_source_down(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        with self._lock:
            summary = self._down.get(request.source)
        if summary is None:
            return None
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"skipped {request.tool_call.name}: {request.source} is unreachable "
                f"this turn ({summary}). Query a different connected source, or "
                "state the outage in your findings instead of retrying it."
            ),
            metadata={"skipped_source": request.source},
        )

    def _mark_source_on_connectivity_error(
        self,
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> None:
        if not result.is_error or request.source in _UNBREAKABLE_SOURCES:
            return None
        error_text = _error_text(result)
        if not _is_connectivity_error(error_text):
            return None
        with self._lock:
            self._down.setdefault(request.source, _summarize(error_text))
        return None


__all__ = ["SourceCircuitBreaker"]
