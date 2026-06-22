"""Loop mechanics and outcome mapping for the investigate node."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.agent.utils.llm_invoke_errors import LLMInvokeFailure
from app.services.agent_llm_client import ToolCall
from app.state.evidence import EvidenceEntry


def tool_call_signature(tool_call: ToolCall) -> str:
    """Stable identity for a tool call: ``name`` + canonicalised arguments."""
    try:
        args = json.dumps(tool_call.input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(tool_call.input)
    return f"{tool_call.name}::{args}"


def duplicate_call_result(tool_call: ToolCall) -> dict[str, Any]:
    """Synthetic result returned in place of re-running an already-seen call."""
    return {
        "suppressed_duplicate": True,
        "tool": tool_call.name,
        "note": (
            f"Skipped: '{tool_call.name}' was already called earlier in this "
            "investigation with identical arguments, so re-running it would return "
            "the same data. Do not call it again. Either call a DIFFERENT tool (or "
            "the same tool with DIFFERENT arguments) to gather new evidence, or "
            "write your final diagnosis."
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
