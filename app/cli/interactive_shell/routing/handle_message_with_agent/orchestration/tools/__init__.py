"""Tool registrations for interactive-shell action execution."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools import (
    assistant_handoff_tool,
    cli_command_tool,
    implementation_tool,
    investigation_tool,
    llm_provider_tool,
    mark_unhandled_tool,
    sample_alert_tool,
    shell_tool,
    slash_tool,
    synthetic_tool,
    task_cancel_tool,
)

__all__ = [
    "assistant_handoff_tool",
    "cli_command_tool",
    "implementation_tool",
    "investigation_tool",
    "llm_provider_tool",
    "mark_unhandled_tool",
    "sample_alert_tool",
    "shell_tool",
    "slash_tool",
    "synthetic_tool",
    "task_cancel_tool",
]
