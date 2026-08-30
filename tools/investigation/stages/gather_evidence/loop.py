"""Loop mechanics and outcome mapping for the investigate node."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

from config.constants.investigation import (
    INVESTIGATION_TOOL_CACHE_MAX_CHARS,
    INVESTIGATION_TOOL_CACHE_MAX_ENTRIES,
    MAX_INVESTIGATION_LOOPS,
)
from core.llm.types import ToolCall
from core.llm_invoke_errors import LLMInvokeFailure
from core.state.evidence import EvidenceEntry
from core.tool import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from infrastructure.text.truncation import truncate

_MAX_CACHED_RESULT_CHARS = 8_000


_ToolCallSignature = tuple[str, Hashable]


def _canonicalize_args(value: Any) -> Hashable:
    """Recursively convert a value into a hashable fingerprint."""
    value_type = type(value)
    if value_type is dict:
        return frozenset((str(key), _canonicalize_args(item)) for key, item in value.items())
    if value_type is list or value_type is tuple:
        return tuple(_canonicalize_args(item) for item in value)
    if value_type is str:
        return str.__str__(value)
    if value is None:
        return None
    if value_type is bool:
        return (bool, value)
    if value_type is int:
        return (int, value)
    if value_type is float:
        return (float, float.hex(value))

    # Provider-decoded arguments use exact JSON scalar/container types. Keep
    # programmatic seed calls compatible without paying these checks normally.
    if isinstance(value, dict):
        return frozenset((str(key), _canonicalize_args(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_canonicalize_args(item) for item in value)
    if isinstance(value, bool):
        return (bool, bool(value))
    if isinstance(value, int):
        return (int, int.__index__(value))
    if isinstance(value, float):
        return (float, float.hex(value))
    if isinstance(value, str):
        return str.__str__(value)
    return str(value)


def tool_call_signature(tool_call: ToolCall) -> _ToolCallSignature:
    """Stable identity for a tool call: ``name`` + canonicalised arguments."""
    args_fingerprint = _canonicalize_args(tool_call.input)
    return (tool_call.name, args_fingerprint)


@dataclass(frozen=True)
class CachedToolResult:
    result: Any
    loop_iteration: int


@dataclass(frozen=True)
class _CallOccurrence:
    tool_call: ToolCall
    signature: _ToolCallSignature
    iteration: int
    order: int
    cached: CachedToolResult | None


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
        self._entries: OrderedDict[_ToolCallSignature, CachedToolResult] = OrderedDict()
        self._entry_chars: dict[_ToolCallSignature, int] = {}
        self._total_chars = 0

    def store(self, signature: _ToolCallSignature, result: Any, *, loop_iteration: int) -> None:
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

    def lookup(self, signature: _ToolCallSignature) -> CachedToolResult | None:
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
        self._occurrences_by_object: dict[int, _CallOccurrence] = {}
        self._occurrences_by_event: dict[tuple[int, str], _CallOccurrence] = {}
        self._prepared_batches: set[tuple[int, tuple[int, ...]]] = set()
        self._duplicate_payloads: dict[int, dict[str, Any]] = {}
        self._recorded_occurrences: set[int] = set()
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
            before_tool_batch=self.prepare_batch,
            before_tool_call=self._before_call,
            after_tool_call=self._after_call,
        )

    def prepare_batch(
        self,
        tool_calls: Sequence[ToolCall],
        *,
        iteration: int | None = None,
    ) -> None:
        """Classify a provider tool batch before runtime start events are emitted."""
        call_iteration = self._iteration if iteration is None else iteration
        batch_key = (call_iteration, tuple(id(tool_call) for tool_call in tool_calls))
        if batch_key in self._prepared_batches:
            return
        self._prepared_batches.add(batch_key)

        fresh_names: list[str] = []
        duplicate_count = 0
        for tool_call in tool_calls:
            signature = tool_call_signature(tool_call)
            cached = self._cache.lookup(signature)
            occurrence = _CallOccurrence(
                tool_call=tool_call,
                signature=signature,
                iteration=call_iteration,
                order=self._next_call_order,
                cached=cached,
            )
            self._next_call_order += 1
            self._occurrences_by_object[id(tool_call)] = occurrence
            self._occurrences_by_event[(call_iteration, tool_call.id)] = occurrence
            if cached is None:
                fresh_names.append(tool_call.name)
            else:
                duplicate_count += 1

        self.executed_hypotheses.append(
            {
                "hypothesis": f"Agent iteration {call_iteration}",
                "actions": fresh_names,
                "loop_iteration": call_iteration,
            }
        )
        if fresh_names:
            self._stagnant_iterations = 0
            return
        if duplicate_count == 0:
            return
        self._stagnant_iterations += 1
        if self._queue_message is not None:
            self._queue_message(self._stagnation_nudge)
        if self._stagnant_iterations >= self._max_stagnant_iterations:
            self.force_conclusion = True

    def is_duplicate_event(self, *, iteration: int, tool_call_id: str) -> bool:
        """Return whether the classified runtime event belongs to a duplicate call."""
        occurrence = self._occurrences_by_event.get((iteration, tool_call_id))
        return occurrence is not None and occurrence.cached is not None

    def was_suppressed(self, tool_call: ToolCall) -> bool:
        """Return whether this exact provider call occurrence was suppressed."""
        occurrence = self._occurrences_by_object.get(id(tool_call))
        return occurrence is not None and occurrence.cached is not None

    def duplicate_payload(self, tool_call: ToolCall) -> dict[str, Any] | None:
        """Return the historical replay payload for a suppressed call."""
        return self._duplicate_payloads.get(id(tool_call))

    def fresh_results(self) -> list[tuple[ToolCall, Any]]:
        """Return newly executed tool results in provider request order."""
        return [self._results_by_order[index] for index in sorted(self._results_by_order)]

    def iteration_for(self, tool_call: ToolCall) -> int:
        """Return the loop iteration associated with this exact call occurrence."""
        occurrence = self._occurrences_by_object.get(id(tool_call))
        return self._iteration if occurrence is None else occurrence.iteration

    def record_runtime_result(
        self,
        *,
        iteration: int,
        tool_call_id: str,
        result: Any,
    ) -> None:
        """Record early executor results that bypass ``after_tool_call`` hooks."""
        occurrence = self._occurrences_by_event.get((iteration, tool_call_id))
        if occurrence is None or occurrence.cached is not None:
            return
        self._record_result(occurrence, result)

    def _before_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        occurrence = self._occurrences_by_object.get(id(request.tool_call))
        if occurrence is None:
            self.prepare_batch([request.tool_call])
            occurrence = self._occurrences_by_object[id(request.tool_call)]
        if occurrence.cached is None:
            return None
        payload = duplicate_call_result(request.tool_call, occurrence.cached)
        with self._lock:
            self._duplicate_payloads[id(request.tool_call)] = payload
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
        occurrence = self._occurrences_by_object.get(id(request.tool_call))
        if occurrence is None:
            self.prepare_batch([request.tool_call])
            occurrence = self._occurrences_by_object[id(request.tool_call)]
        self._record_result(occurrence, result.compat_payload())

    def _record_result(self, occurrence: _CallOccurrence, payload: Any) -> None:
        with self._lock:
            occurrence_key = id(occurrence.tool_call)
            if occurrence_key in self._recorded_occurrences:
                return
            self._recorded_occurrences.add(occurrence_key)
            self._cache.store(
                occurrence.signature,
                payload,
                loop_iteration=occurrence.iteration,
            )
            self._results_by_order[occurrence.order] = (
                occurrence.tool_call,
                payload,
            )
            self.current_evidence[occurrence.tool_call.name] = payload
            if occurrence.iteration == 0 and not self._checkpoint_sent:
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
