"""Stable action-tool names used by scenario fixtures and tests."""

from __future__ import annotations

from enum import StrEnum


class ToolKind(StrEnum):
    """Closed set of action-tool kinds.

    Scenario YAML and harness fixtures reference these by plain string
    (e.g. ``kind: slash``); StrEnum keeps that working (members compare
    and hash equal to their string value) while making the set explicit.
    """

    SLASH = "slash"
    SHELL = "shell"
    INVESTIGATION = "investigation"
    ALERT = "alert"
    SAMPLE_ALERT = "sample_alert"
    SYNTHETIC_TEST = "synthetic_test"
    TASK_CANCEL = "task_cancel"
    CLI_COMMAND = "cli_command"
    IMPLEMENTATION = "implementation"
    LLM_PROVIDER = "llm_provider"
    ASSISTANT_HANDOFF = "assistant_handoff"


TOOL_KIND_TO_NAME: dict[ToolKind, str] = {
    ToolKind.SLASH: "slash_invoke",
    ToolKind.SHELL: "shell_run",
    ToolKind.INVESTIGATION: "investigation_start",
    ToolKind.ALERT: "alert_sample",
    ToolKind.SAMPLE_ALERT: "alert_sample",
    ToolKind.SYNTHETIC_TEST: "synthetic_run",
    ToolKind.TASK_CANCEL: "task_cancel",
    ToolKind.CLI_COMMAND: "cli_exec",
    ToolKind.IMPLEMENTATION: "code_implement",
    ToolKind.LLM_PROVIDER: "llm_set_provider",
    ToolKind.ASSISTANT_HANDOFF: "assistant_handoff",
}

__all__ = ["TOOL_KIND_TO_NAME", "ToolKind"]
