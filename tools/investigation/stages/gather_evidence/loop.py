"""Loop mechanics and outcome mapping for the investigate node."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

from config.constants.investigation import (
    INVESTIGATION_TOOL_CACHE_MAX_CHARS,
    INVESTIGATION_TOOL_CACHE_MAX_ENTRIES,
    MAX_INVESTIGATION_LOOPS,
)
from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from core.llm.types import ToolCall
from core.llm_invoke_errors import LLMInvokeFailure
from core.state.evidence import EvidenceEntry
from platform.common.truncation import truncate

_MAX_CACHED_RESULT_CHARS = 8_000


def tool_call_signature(tool_call: ToolCall) -> str:
    """Stable identity for a tool call: ``name`` + canonicalised arguments."""
    try:
        args = json.dumps(tool_call.input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(tool_call.input)
    return f"{tool_call.name}::{args}"


@dataclass(frozen=True)
class CachedToolResult:
    result: Any
    loop_iteration: int


def _estimate_payload_chars(result: Any) -> int:
    """Approximate serialized size for cache byte budgeting."""
    try:
        return len(json.dumps(result, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(repr(result))


class InvestigationToolCallCache:
    """Bounded per-investigation cache of tool results keyed by signature.

    Lookup stays O(1). Eviction is LRU by entry count and approximate payload
    chars so long investigations cannot retain every full result forever.
    """

    def __init__(
        self,
        *,
        max_entries: int = INVESTIGATION_TOOL_CACHE_MAX_ENTRIES,
        max_total_chars: int = INVESTIGATION_TOOL_CACHE_MAX_CHARS,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if max_total_chars < 1:
            raise ValueError("max_total_chars must be >= 1")
        self._max_entries = max_entries
        self._max_total_chars = max_total_chars
        self._entries: OrderedDict[str, CachedToolResult] = OrderedDict()
        self._entry_chars: dict[str, int] = {}
        self._total_chars = 0

    def store(self, signature: str, result: Any, *, loop_iteration: int) -> None:
        if signature in self._entries:
            return
        stored = result
        size = _estimate_payload_chars(stored)
        # Hard-bound pathological payloads: dup replay only needs a preview.
        # Truncation wrapper keys (~80 chars) must still fit under the budget.
        if size > self._max_total_chars:
            preview_budget = max(32, self._max_total_chars - 96)
            stored = _bounded_cached_result_payload(
                stored,
                max_chars=min(_MAX_CACHED_RESULT_CHARS, preview_budget),
            )
            size = _estimate_payload_chars(stored)
            if size > self._max_total_chars:
                stored = {
                    "_truncated_for_duplicate_replay": True,
                    "preview": "",
                    "note": "omitted: exceeded investigation tool cache char budget",
                }
                size = _estimate_payload_chars(stored)
        while self._entries and (
            len(self._entries) >= self._max_entries
            or self._total_chars + size > self._max_total_chars
        ):
            self._evict_oldest()
        self._entries[signature] = CachedToolResult(result=stored, loop_iteration=loop_iteration)
        self._entry_chars[signature] = size
        self._total_chars += size

    def lookup(self, signature: str) -> CachedToolResult | None:
        cached = self._entries.get(signature)
        if cached is not None:
            self._entries.move_to_end(signature)
        return cached

    def _evict_oldest(self) -> None:
        oldest_signature, _ = self._entries.popitem(last=False)
        self._total_chars -= self._entry_chars.pop(oldest_signature)


def _bounded_cached_result_payload(result: Any, *, max_chars: int) -> Any:
    """Bound duplicate replay size; the cache still stores the full first result."""
    try:
        serialized = json.dumps(result, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = repr(result)
    if len(serialized) <= max_chars:
        return result
    return {
        "_truncated_for_duplicate_replay": True,
        "preview": truncate(serialized, max_chars),
    }


def duplicate_call_result(tool_call: ToolCall, cached: CachedToolResult) -> dict[str, Any]:
    """Return a wrapped cached result instead of re-running an identical tool call."""
    if cached.loop_iteration < 0:
        when = "during seed evidence collection"
    else:
        when = f"in lap {cached.loop_iteration + 1}"

    return {
        "suppressed_duplicate": True,
        "reused_cached_result": True,
        "tool": tool_call.name,
        "first_called_at_loop": cached.loop_iteration,
        "note": (
            f"You already called '{tool_call.name}' with identical arguments {when}. "
            "Reused the cached result below instead of fetching again. Do not call it "
            "again with the same arguments — either call a DIFFERENT tool (or the same "
            "tool with DIFFERENT arguments) to gather new evidence, or write your final "
            "diagnosis."
        ),
        "cached_result": _bounded_cached_result_payload(
            cached.result,
            max_chars=_MAX_CACHED_RESULT_CHARS,
        ),
    }


class InvestigationLoopController:
    """Preserve investigation-specific replay and stagnation policy around ``Agent.run``."""

    def __init__(
        self,
        *,
        stagnation_nudge: str,
        checkpoint_nudge: str,
        max_stagnant_iterations: int,
    ) -> None:
        self._cache = InvestigationToolCallCache()
        self._stagnation_nudge = stagnation_nudge
        self._checkpoint_nudge = checkpoint_nudge
        self._max_stagnant_iterations = max_stagnant_iterations
        self._queue_message: Callable[[str], None] | None = None
        self._iteration = 0
        self._duplicate_calls: dict[str, CachedToolResult] = {}
        self._suppressed_calls: set[tuple[str, str]] = set()
        self._duplicate_payloads: dict[tuple[str, str], dict[str, Any]] = {}
        self._call_iterations: dict[str, int] = {}
        self._call_order: dict[str, int] = {}
        self._results_by_order: dict[int, tuple[ToolCall, Any]] = {}
        self._next_call_order = 0
        self._lock = Lock()
        self._checkpoint_sent = False
        self._stagnant_iterations = 0
        self.force_conclusion = False
        self.executed_hypotheses: list[dict[str, Any]] = []
        self.current_evidence: dict[str, Any] = {}

    def bind_queue_message(self, queue_message: Callable[[str], None]) -> None:
        """Bind the shared agent's steering seam after construction."""
        self._queue_message = queue_message

    def begin_iteration(self, iteration: int) -> None:
        """Record the runtime iteration used by the next requested tool batch."""
        self._iteration = iteration

    def record_seed(self, tool_call: ToolCall, result: Any) -> None:
        """Make a seed result eligible for duplicate replay in the shared loop."""
        self._cache.store(tool_call_signature(tool_call), result, loop_iteration=-1)

    def hooks(self) -> ToolExecutionHooks:
        """Return execution hooks that suppress duplicate investigation calls."""
        return ToolExecutionHooks(
            before_tool_batch=self._before_batch,
            before_tool_call=self._before_call,
            after_tool_call=self._after_call,
        )

    def is_duplicate(self, tool_call_id: str) -> bool:
        """Return whether the current run suppressed ``tool_call_id``."""
        return tool_call_id in self._duplicate_calls

    def was_suppressed(self, tool_call: ToolCall) -> bool:
        """Return whether ``tool_call`` was suppressed at any point in this run."""
        return (tool_call.id, tool_call_signature(tool_call)) in self._suppressed_calls

    def duplicate_payload(self, tool_call: ToolCall) -> dict[str, Any] | None:
        """Return the historical replay payload for a suppressed call."""
        return self._duplicate_payloads.get((tool_call.id, tool_call_signature(tool_call)))

    def fresh_results(self) -> list[tuple[ToolCall, Any]]:
        """Return newly executed tool results in provider request order."""
        return [self._results_by_order[index] for index in sorted(self._results_by_order)]

    def iteration_for(self, tool_call_id: str) -> int:
        """Return the loop iteration associated with a requested tool call."""
        return self._call_iterations.get(tool_call_id, self._iteration)

    def _before_batch(self, tool_calls: Sequence[ToolCall]) -> None:
        duplicate_calls: dict[str, CachedToolResult] = {}
        fresh_names: list[str] = []
        for tool_call in tool_calls:
            self._call_iterations[tool_call.id] = self._iteration
            self._call_order[tool_call.id] = self._next_call_order
            self._next_call_order += 1
            cached = self._cache.lookup(tool_call_signature(tool_call))
            if cached is None:
                fresh_names.append(tool_call.name)
            else:
                duplicate_calls[tool_call.id] = cached
        self._duplicate_calls = duplicate_calls
        self._suppressed_calls.update(
            (tool_call.id, tool_call_signature(tool_call))
            for tool_call in tool_calls
            if tool_call.id in duplicate_calls
        )
        self.executed_hypotheses.append(
            {
                "hypothesis": f"Agent iteration {self._iteration}",
                "actions": fresh_names,
                "loop_iteration": self._iteration,
            }
        )
        if fresh_names:
            self._stagnant_iterations = 0
            return
        self._stagnant_iterations += 1
        if self._queue_message is not None:
            self._queue_message(self._stagnation_nudge)
        if self._stagnant_iterations >= self._max_stagnant_iterations:
            self.force_conclusion = True

    def _before_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        cached = self._duplicate_calls.get(request.tool_call.id)
        if cached is None:
            return None
        payload = duplicate_call_result(request.tool_call, cached)
        with self._lock:
            self._duplicate_payloads[
                (request.tool_call.id, tool_call_signature(request.tool_call))
            ] = payload
        return BeforeToolCallResult(
            blocked=True,
            reason=json.dumps(payload, default=str),
            details=payload,
            metadata={"suppressed_duplicate": True},
        )

    def _after_call(
        self,
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> None:
        with self._lock:
            payload = result.compat_payload()
            self._cache.store(
                tool_call_signature(request.tool_call),
                payload,
                loop_iteration=self.iteration_for(request.tool_call.id),
            )
            self._results_by_order[self._call_order[request.tool_call.id]] = (
                request.tool_call,
                payload,
            )
            self.current_evidence[request.tool_call.name] = payload
            if self._iteration == 0 and not self._checkpoint_sent:
                self._checkpoint_sent = True
                if self._queue_message is not None:
                    self._queue_message(self._checkpoint_nudge)


def degraded_investigation_from_llm_failure(
    failure: LLMInvokeFailure,
    *,
    err: BaseException,
    tracker: Any,
    _emit: Callable[[str, dict[str, Any]], None],
    evidence: dict[str, Any],
    evidence_entries: list[EvidenceEntry],
    messages: list[dict[str, Any]],
    executed_hypotheses: list[dict[str, Any]],
    tool_context: dict[str, Any],
    investigation_loop_count: int = 0,
) -> dict[str, Any]:
    """Return a partial investigation state when an LLM invoke fails operatively."""
    tracker.error("investigation_agent", message=failure.tracker_message)
    error_msg = f"Error: {failure.user_message}"
    _emit(
        "agent_end",
        {
            "root_cause": error_msg,
            "validity_score": 0.0,
            "root_cause_category": failure.root_cause_category,
        },
    )
    updates = {
        "root_cause": error_msg,
        "root_cause_category": failure.root_cause_category,
        "causal_chain": [f"LLM invoke failed: {err!s}"],
        "validated_claims": [],
        "non_validated_claims": [],
        "remediation_steps": failure.remediation_steps,
        "validity_score": 0.0,
        "investigation_recommendations": [],
        "evidence": evidence,
        "evidence_entries": [e.model_dump() for e in evidence_entries],
        "agent_messages": messages,
        "executed_hypotheses": executed_hypotheses,
        "investigation_loop_count": max(0, int(investigation_loop_count)),
        "investigation_iteration_cap": MAX_INVESTIGATION_LOOPS,
    }
    updates.update(tool_context)
    return updates
