"""Assistant handoff pseudo-tool for non-executable requests."""

from __future__ import annotations

from typing import Any

from tools.interactive_shell.contracts import (
    ToolContext,
    execute_with_repl_context,
    object_schema,
    string_property,
)
from tools.registered_tool import RegisteredTool


def execute_assistant_handoff_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
    _ = args
    _ = ctx
    # Handoffs are informational planning outputs and intentionally
    # execute no terminal side effects.
    return True


def run_assistant_handoff(*, content: str, context: Any) -> dict[str, Any]:
    return execute_with_repl_context(
        {"content": content},
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
                    "or non-executable requests."
                ),
                min_length=1,
            )
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
