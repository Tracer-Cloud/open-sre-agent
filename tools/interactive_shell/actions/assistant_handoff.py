"""Assistant handoff pseudo-tool for non-executable requests."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    execute_with_action_context,
    object_schema,
    string_array_property,
    string_property,
)
from core.agent_harness.turns.evidence_kind import EVIDENCE_KIND_VALUES
from core.agent_harness.turns.handoff_keys import HandoffField
from core.domain.types.tools import ToolSurface
from core.tool_framework.registered_tool import RegisteredTool


def execute_assistant_handoff_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
    _ = args
    _ = ctx
    # Handoffs are informational planning outputs and intentionally
    # execute no terminal side effects.
    return True


def run_assistant_handoff(
    *,
    content: str,
    context: Any,
    requires_gather: bool = True,
    evidence_kind: str | None = None,
    session_goal: bool | None = None,
    session_goal_max_turns: int | None = None,
    session_goal_items: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        HandoffField.CONTENT: content,
        HandoffField.REQUIRES_GATHER: requires_gather,
    }
    if evidence_kind is not None:
        payload[HandoffField.EVIDENCE_KIND] = evidence_kind
    if session_goal is not None:
        payload[HandoffField.SESSION_GOAL] = session_goal
    if session_goal_max_turns is not None:
        payload[HandoffField.SESSION_GOAL_MAX_TURNS] = session_goal_max_turns
    if session_goal_items is not None:
        payload[HandoffField.SESSION_GOAL_ITEMS] = session_goal_items
    return execute_with_action_context(
        payload,
        context,
        execute_assistant_handoff_tool,
    )


assistant_handoff_tool = RegisteredTool(
    name="assistant_handoff",
    description=(
        "Mark a request as non-executable and hand off to assistant response generation. "
        "Use for informational, conversational, ambiguous, or non-actionable requests, "
        "including a bare pasted alert JSON/YAML/key-value blob or bare incident statement "
        "when the user did not explicitly ask to investigate, analyze, diagnose, RCA, or "
        "root-cause it. For stream-only docs/how-to/greeting chat set requires_gather=false; "
        "for metric/count asks set evidence_kind=metric_read (leave requires_gather true); "
        "for multi-step continuation set session_goal (and optional session_goal_items)."
    ),
    input_schema=object_schema(
        properties={
            HandoffField.CONTENT: string_property(
                description=(
                    "Concise assistant handoff text for informational, ambiguous, "
                    "or non-executable requests. Prefer structured tags when the "
                    "topic is known — e.g. docs:datadog_setup, chat:greeting, "
                    "provider:local_llama_connect for vague local-model setup. "
                    "Do not bury evidence_kind / session_goal in prose when the "
                    "dedicated fields below apply — set those fields instead."
                ),
                min_length=1,
            ),
            HandoffField.EVIDENCE_KIND: string_property(
                description=(
                    "Closed evidence category for harness policy. Use metric_read for "
                    "product-analytics metrics/counts over a time window; incident for "
                    "bare symptom/incident handoffs; setup for connect/configure asks; "
                    "other only when none of those apply. Enum is derived from "
                    "EvidenceKind — do not hard-code a parallel list."
                ),
                enum=EVIDENCE_KIND_VALUES,
            ),
            HandoffField.SESSION_GOAL: {
                "type": "boolean",
                "description": (
                    "REQUIRED true for multi-step / keep-going chat checklists "
                    "so the host continues across turns without asking. Omit "
                    "only for a single-turn answer."
                ),
            },
            HandoffField.SESSION_GOAL_MAX_TURNS: {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Session-goal turn cap for the goal. Omit to use the default. Requires session_goal."
                ),
            },
            HandoffField.SESSION_GOAL_ITEMS: string_array_property(
                description=(
                    "Checklist success criteria for the SessionGoal "
                    "(one string per item, in order). Requires session_goal."
                ),
            ),
            HandoffField.REQUIRES_GATHER: {
                "type": "boolean",
                "description": (
                    "Whether the assistant needs a live evidence-gather (tool) "
                    "pass before answering. Default true for asks that need live "
                    "integrations data. Set false for stream-only replies: pure "
                    "docs/how-to/explain/greeting chat with no live evidence, OR "
                    "when this turn's other tools already produced everything the "
                    "reply needs. False skips the gather agent and goes straight "
                    "to stream_answer."
                ),
            },
        },
        required=(HandoffField.CONTENT,),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_assistant_handoff,
)


__all__ = ["assistant_handoff_tool", "execute_assistant_handoff_tool"]
