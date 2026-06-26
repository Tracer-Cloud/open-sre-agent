"""Tool registrations for interactive-shell action execution."""

from __future__ import annotations

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolEntry,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools import (
    assistant_handoff_tool,
    cli_command_tool,
    implementation_tool,
    investigation_tool,
    llm_provider_tool,
    sample_alert_tool,
    shell_tool,
    slash_tool,
    synthetic_tool,
    task_cancel_tool,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools.catalog import (
    ACTION_TOOL_CATALOG,
)


def action_tool_entries() -> tuple[ToolEntry, ...]:
    """Return all tool entries in one explicit, deterministic order."""
    return ACTION_TOOL_CATALOG


__all__ = [
    "ACTION_TOOL_CATALOG",
    "action_tool_entries",
    "assistant_handoff_tool",
    "cli_command_tool",
    "implementation_tool",
    "investigation_tool",
    "llm_provider_tool",
    "sample_alert_tool",
    "shell_tool",
    "slash_tool",
    "synthetic_tool",
    "task_cancel_tool",
]
