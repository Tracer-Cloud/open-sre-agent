"""Per-turn circuit breaker over tools that fail to connect.

Installed as :class:`~core.execution.ToolExecutionHooks` on a bounded tool
loop: the first transport-level failure (connect timeout, connection refused)
marks **that tool** unreachable for the rest of the gather run. Later calls to
the same tool are blocked immediately with a reason that steers the model to
other tools or sources instead of re-paying the connect timeout.

Marks are tool-scoped on purpose. A vendor often exposes several endpoints; one
failing tool must not suppress reachable siblings. A success for a tool is
sticky for the turn: a concurrent connectivity failure that finishes after
that success must not recreate the mark (otherwise the next gather iteration
blocks a tool that just returned evidence). Application / downstream errors
still do not trip the breaker at all.
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

# When these appear with a connectivity marker, the failure is about a target
# the reachable vendor could not reach — not the vendor transport itself.
_DOWNSTREAM_VETO_MARKERS = (
    "datasource",
    "data source",
    "upstream",
    "backend",
    "target connection",
    "peer connection",
)

# Tools with no meaningful source identity — still allow tool-level marks.
_UNBREAKABLE_SOURCES = frozenset({"", "unknown"})

_REASON_SUMMARY_CHARS = 160


def _error_text(result: ToolExecutionResult) -> str:
    if isinstance(result.content, str):
        return result.content
    return str(result.details if result.details is not None else result.content)


def _is_connectivity_error(error_text: str) -> bool:
    lowered = error_text.lower()
    if not any(marker in lowered for marker in _CONNECTIVITY_ERROR_MARKERS):
        return False
    # A reachable service reporting that *its* target is down must not poison
    # tools for the rest of the turn.
    return not any(marker in lowered for marker in _DOWNSTREAM_VETO_MARKERS)


def _summarize(error_text: str) -> str:
    single_line = " ".join(error_text.split())
    if len(single_line) <= _REASON_SUMMARY_CHARS:
        return single_line
    return single_line[:_REASON_SUMMARY_CHARS] + "…"


class SourceCircuitBreaker:
    """Tracks tools that failed at the transport level within one run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tool_down: dict[str, str] = {}
        # Sticky for the gather turn: once a tool succeeds, concurrent or later
        # connectivity failures must not re-mark it.
        self._tool_ok: set[str] = set()

    def hooks(self) -> ToolExecutionHooks:
        """Return execution hooks enforcing the breaker on a tool loop."""
        return ToolExecutionHooks(
            before_tool_call=self._skip_if_tool_down,
            after_tool_call=self._mark_tool_on_connectivity_error,
        )

    def _skip_if_tool_down(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool_name = request.tool_call.name
        with self._lock:
            summary = self._tool_down.get(tool_name)
        if summary is None:
            return None
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"skipped {tool_name}: tool is unreachable "
                f"this turn ({summary}). Query a different connected source or "
                "tool, or state the outage in your findings instead of retrying it."
            ),
            metadata={
                "skipped_source": request.source,
                "skipped_tool": tool_name,
            },
        )

    def _mark_tool_on_connectivity_error(
        self,
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> None:
        tool_name = request.tool_call.name
        if not result.is_error:
            with self._lock:
                self._tool_ok.add(tool_name)
                self._tool_down.pop(tool_name, None)
            return None
        if request.source in _UNBREAKABLE_SOURCES:
            return None
        error_text = _error_text(result)
        if not _is_connectivity_error(error_text):
            return None
        summary = _summarize(error_text)
        with self._lock:
            # Success is sticky: do not recreate a mark after the tool answered.
            if tool_name in self._tool_ok:
                return None
            self._tool_down.setdefault(tool_name, summary)
        return None


__all__ = ["SourceCircuitBreaker"]
