"""Loop mechanics and outcome mapping for the investigate node."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config.constants.investigation import (
    INVESTIGATION_TOOL_CACHE_MAX_CHARS,
    INVESTIGATION_TOOL_CACHE_MAX_ENTRIES,
    MAX_INVESTIGATION_LOOPS,
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
