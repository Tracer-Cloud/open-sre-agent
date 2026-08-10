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
    session_goal: str | None = None,
    session_goal_items: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": content, "requires_gather": requires_gather}
    if evidence_kind is not None:
        payload["evidence_kind"] = evidence_kind
    if session_goal is not None:
        payload["session_goal"] = session_goal
    if session_goal_items is not None:
        payload["session_goal_items"] = session_goal_items
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
        "root-cause it. For metric/count asks set evidence_kind=metric_read; for multi-step "
        "continuation set session_goal (and optional session_goal_items)."
    ),
    input_schema=object_schema(
        properties={
            "content": string_property(
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
            "evidence_kind": string_property(
                description=(
                    "Closed evidence category for harness policy. Use metric_read for "
                    "product-analytics metrics/counts over a time window; incident for "
                    "bare symptom/incident handoffs; setup for connect/configure asks; "
                    "other only when none of those apply. Enum is derived from "
                    "EvidenceKind — do not hard-code a parallel list."
                ),
                enum=EVIDENCE_KIND_VALUES,
            ),
            "session_goal": string_property(
                description=(
                    "Attach an outer multi-turn SessionGoal. Use continue, or "
                    "max_turns=<n>, or max_turns=<n>;steps=<n>. Omit when the ask "
                    "is a single-turn answer."
                ),
            ),
            "session_goal_items": string_array_property(
                description=(
                    "Checklist success criteria for the outer SessionGoal "
                    "(one string per item, in order). Requires session_goal."
                ),
            ),
            "requires_gather": {
                "type": "boolean",
                "description": (
                    "Whether the assistant needs a live evidence-gather pass before "
                    "answering. Default true. Set false ONLY when this turn's tool "
                    "work already produced everything the reply needs and the "
                    "handoff merely explains that outcome — fetching fresh data "
                    "would answer a different question."
                ),
            },
        },
        required=("content",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_assistant_handoff,
)


__all__ = ["assistant_handoff_tool", "execute_assistant_handoff_tool"]
