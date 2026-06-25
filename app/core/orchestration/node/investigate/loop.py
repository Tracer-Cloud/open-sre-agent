"""Loop mechanics and outcome mapping for the investigate node."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.domain.state.evidence import EvidenceEntry
from app.core.runtime.llm_invoke_errors import LLMInvokeFailure
from app.services.agent_llm_client import ToolCall
from app.utils.truncation import truncate

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


class InvestigationToolCallCache:
    """Per-investigation cache of tool results keyed by ``tool_call_signature``."""

    def __init__(self) -> None:
        self._entries: dict[str, CachedToolResult] = {}

    def store(self, signature: str, result: Any, *, loop_iteration: int) -> None:
        if signature in self._entries:
            return
        self._entries[signature] = CachedToolResult(result=result, loop_iteration=loop_iteration)

    def lookup(self, signature: str) -> CachedToolResult | None:
        return self._entries.get(signature)


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
    }
    updates.update(tool_context)
    return updates
