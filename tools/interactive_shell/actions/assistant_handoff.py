"""Assistant handoff pseudo-tool for non-executable requests."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool


def execute_assistant_handoff_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
    _ = args
    _ = ctx
    # Handoffs are informational planning outputs and intentionally
    # execute no terminal side effects.
    return True


def run_assistant_handoff(
    *, content: str, context: Any, requires_gather: bool = True
) -> dict[str, Any]:
    return execute_with_action_context(
        {"content": content, "requires_gather": requires_gather},
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
        "root-cause it."
    ),
    input_schema=object_schema(
        properties={
            "content": string_property(
                description=(
                    "Concise assistant handoff text for informational, ambiguous, "
                    "or non-executable requests. Prefer structured tags when the "
                    "topic is known — e.g. docs:datadog_setup, chat:greeting, "
                    "provider:local_llama_connect for vague local-model setup."
                ),
                min_length=1,
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
