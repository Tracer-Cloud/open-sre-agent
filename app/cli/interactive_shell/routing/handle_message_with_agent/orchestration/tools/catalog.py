"""Central tool catalog for interactive-shell action execution."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolEntry,
)

from . import (
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

# One explicit composition root for tool ordering and availability.
ACTION_TOOL_CATALOG: tuple[ToolEntry, ...] = (
    slash_tool.TOOL_ENTRY,
    shell_tool.TOOL_ENTRY,
    investigation_tool.TOOL_ENTRY,
    sample_alert_tool.TOOL_ENTRY,
    synthetic_tool.TOOL_ENTRY,
    task_cancel_tool.TOOL_ENTRY,
    cli_command_tool.TOOL_ENTRY,
    implementation_tool.TOOL_ENTRY,
    llm_provider_tool.TOOL_ENTRY,
    assistant_handoff_tool.TOOL_ENTRY,
)


__all__ = ["ACTION_TOOL_CATALOG"]
