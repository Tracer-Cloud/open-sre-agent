"""``mark_unhandled`` pseudo-tool for partial-handling signalling.

This tool produces no terminal side effects. It exists solely so the LLM
planner can flag — in the same tool-call response — that some clause of
the user's request could not be mapped to an executable tool. OpenAI and
OpenAI-compatible providers clear the free-text body whenever a model
emits ``tool_calls``, so the previous "UNHANDLED:" plain-text convention
silently dropped whenever the planner also matched at least one valid
tool. Routing that signal through a dedicated tool keeps the structured
contract intact while still letting the LLM ship the matched action.

The planner detects calls to this tool in
:func:`llm_action_planner._parse_tool_plan` and sets
``has_unhandled`` accordingly; no executable action is appended.
"""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)


def execute_mark_unhandled_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    _ = args
    _ = ctx
    return True


TOOL_ENTRY = ToolEntry(
    name="mark_unhandled",
    description=(
        "Signal that part of the user's request could not be mapped to an "
        "executable tool. Call this in addition to any matched tool calls "
        "whenever the prompt contains a clause (joined by 'and', 'then', "
        "etc.) that is nonsensical, ambiguous, or outside OpenSRE's scope. "
        'MUST be called for partial-handling requests like "show me '
        'connected services and sing a song" — emitting the matched '
        "slash_invoke alone is treated as a fully-handled request and "
        "silently drops the unmatched clause."
    ),
    input_schema=object_schema(
        properties={
            "reason": string_property(
                description=(
                    "Brief description of which portion of the request "
                    "was not mapped and why (e.g. \"'sing a song' is not "
                    'an executable OpenSRE operation").'
                ),
                min_length=1,
            )
        },
        required=("reason",),
    ),
    execution_tier=ExecutionTier.SAFE,
    execute=execute_mark_unhandled_action,
)


__all__ = ["TOOL_ENTRY", "execute_mark_unhandled_action"]
