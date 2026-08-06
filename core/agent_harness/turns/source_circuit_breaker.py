"""Per-turn circuit breaker over tool sources that fail to connect.

Installed as :class:`~core.execution.ToolExecutionHooks` on a bounded tool
loop: the first transport-level failure (connect timeout, connection refused)
marks that tool's source unreachable for the rest of the run, and later calls
to the same source are blocked immediately with a reason that steers the model
to healthy sources instead of re-paying the connect timeout.

Scope is intentionally conservative when the vendor already answered this turn:
a connectivity-looking error after a success only skips that tool, not every
other tool under the same ``source``. A dead host (no prior success) still
trips the whole source so gather does not stack timeouts across its tools.
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

# Sources that cannot be meaningfully marked down as a unit.
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
    # the vendor for the rest of the turn.
    if any(marker in lowered for marker in _DOWNSTREAM_VETO_MARKERS):
        return False
    return True


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
        self._tool_down: dict[str, str] = {}
        self._source_ok: set[str] = set()

    def hooks(self) -> ToolExecutionHooks:
        """Return execution hooks enforcing the breaker on a tool loop."""
        return ToolExecutionHooks(
            before_tool_call=self._skip_if_source_down,
            after_tool_call=self._mark_source_on_connectivity_error,
        )

    def _skip_if_source_down(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        with self._lock:
            tool_summary = self._tool_down.get(request.tool_call.name)
            source_summary = self._down.get(request.source)
        summary = tool_summary if tool_summary is not None else source_summary
        if summary is None:
            return None
        scope = request.tool_call.name if tool_summary is not None else request.source
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"skipped {request.tool_call.name}: {scope} is unreachable "
                f"this turn ({summary}). Query a different connected source, or "
                "state the outage in your findings instead of retrying it."
            ),
            metadata={
                "skipped_source": request.source,
                "skipped_tool": request.tool_call.name if tool_summary is not None else "",
            },
        )

    def _mark_source_on_connectivity_error(
        self,
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> None:
        if not result.is_error:
            if request.source not in _UNBREAKABLE_SOURCES:
                with self._lock:
                    self._source_ok.add(request.source)
            return None
        if request.source in _UNBREAKABLE_SOURCES:
            return None
        error_text = _error_text(result)
        if not _is_connectivity_error(error_text):
            return None
        summary = _summarize(error_text)
        with self._lock:
            # Vendor already answered this turn → another endpoint may still be
            # fine. Only skip the failing tool so gather keeps reachable evidence.
            if request.source in self._source_ok:
                self._tool_down.setdefault(request.tool_call.name, summary)
            else:
                self._down.setdefault(request.source, summary)
        return None


__all__ = ["SourceCircuitBreaker"]
