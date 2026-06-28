"""Central tool catalog for interactive-shell tool execution."""

from __future__ import annotations

from tools.interactive_shell.actions import (
    assistant_handoff,
    cli_command,
    implementation,
    investigation,
    llm_provider,
    sample_alert,
    shell,
    slash,
    synthetic,
    task_cancel,
)
from tools.interactive_shell.contracts import (
    ToolEntry,
)

# One explicit composition root for tool ordering and availability.
TOOL_CATALOG: tuple[ToolEntry, ...] = (
    slash.TOOL_ENTRY,
    shell.TOOL_ENTRY,
    investigation.TOOL_ENTRY,
    sample_alert.TOOL_ENTRY,
    synthetic.TOOL_ENTRY,
    task_cancel.TOOL_ENTRY,
    cli_command.TOOL_ENTRY,
    implementation.TOOL_ENTRY,
    llm_provider.TOOL_ENTRY,
    assistant_handoff.TOOL_ENTRY,
)


__all__ = ["TOOL_CATALOG"]
