"""Tool registrations for interactive-shell action execution.

Submodule re-exports use ``import <path> as <name>`` so the package
``__init__`` only depends on its child modules — not on itself.
The earlier ``from <self-package> import <submodule>`` form created a
Tarjan-visible self-loop in the import graph even though Python
resolved it at runtime via the package namespace.
"""

from __future__ import annotations

import cli.interactive_shell.harness.orchestration.tools.assistant_handoff_tool as assistant_handoff_tool
import cli.interactive_shell.harness.orchestration.tools.cli_command_tool as cli_command_tool
import cli.interactive_shell.harness.orchestration.tools.implementation_tool as implementation_tool
import cli.interactive_shell.harness.orchestration.tools.investigation_tool as investigation_tool
import cli.interactive_shell.harness.orchestration.tools.llm_provider_tool as llm_provider_tool
import cli.interactive_shell.harness.orchestration.tools.sample_alert_tool as sample_alert_tool
import cli.interactive_shell.harness.orchestration.tools.shell_tool as shell_tool
import cli.interactive_shell.harness.orchestration.tools.slash_tool as slash_tool
import cli.interactive_shell.harness.orchestration.tools.synthetic_tool as synthetic_tool
import cli.interactive_shell.harness.orchestration.tools.task_cancel_tool as task_cancel_tool
from cli.interactive_shell.harness.orchestration.tool_contracts import (
    ToolEntry,
)
from cli.interactive_shell.harness.orchestration.tools.catalog import (
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
