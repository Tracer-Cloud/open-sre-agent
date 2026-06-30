"""Stable action-tool names used by scenario fixtures and tests."""

from __future__ import annotations

from typing import Literal

ToolKind = Literal[
    "slash",
    "shell",
    "investigation",
    "alert",
    "sample_alert",
    "synthetic_test",
    "task_cancel",
    "cli_command",
    "implementation",
    "llm_provider",
    "assistant_handoff",
]

TOOL_KIND_TO_NAME: dict[ToolKind, str] = {
    "slash": "slash_invoke",
    "shell": "shell_run",
    "investigation": "investigation_start",
    "alert": "alert_sample",
    "sample_alert": "alert_sample",
    "synthetic_test": "synthetic_run",
    "task_cancel": "task_cancel",
    "cli_command": "cli_exec",
    "implementation": "code_implement",
    "llm_provider": "llm_set_provider",
    "assistant_handoff": "assistant_handoff",
}

__all__ = ["TOOL_KIND_TO_NAME", "ToolKind"]
