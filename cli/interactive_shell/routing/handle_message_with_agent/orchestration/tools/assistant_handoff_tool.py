"""Assistant handoff pseudo-tool for non-executable requests."""

from __future__ import annotations

from typing import Any

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)


def execute_assistant_handoff_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    _ = args
    _ = ctx
    # Handoff actions are informational planning outputs and intentionally
    # execute no terminal side effects.
    return True


TOOL_ENTRY = ToolEntry(
    name="assistant_handoff",
    description="Mark a request as non-executable and hand off to assistant response generation.",
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
    execution_tier=ExecutionTier.SAFE,
    execute=execute_assistant_handoff_action,
)


__all__ = ["TOOL_ENTRY", "execute_assistant_handoff_action"]
