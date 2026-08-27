"""Stable action-tool names used by scenario fixtures, tests, and observers."""

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
    SESSION_GOAL = "session_goal"


class ActionToolName(StrEnum):
    """Registered ``RegisteredTool.name`` values for interactive-shell actions.

    Observer dispatch and :data:`TOOL_KIND_TO_NAME` share this closed set so a
    renamed tool cannot silently miss a render branch.
    """

    ALERT_SAMPLE = "alert_sample"
    ASK_USER_CHOICE = "ask_user_choice"
    CLI_EXEC = "cli_exec"
    CODE_IMPLEMENT = "code_implement"
    FIX_SENTRY_ISSUE_START = "fix_sentry_issue_start"
    INVESTIGATION_START = "investigation_start"
    LLM_SET_PROVIDER = "llm_set_provider"
    PROPOSE_SCHEDULED_DELIVERY = "propose_scheduled_delivery"
    SESSION_GOAL_SET = "session_goal_set"
    SHELL_RUN = "shell_run"
    SKILL_VIEW = "skill_view"
    SLASH_INVOKE = "slash_invoke"
    SYNTHETIC_RUN = "synthetic_run"
    TASK_CANCEL = "task_cancel"
    UPDATE_PLAN = "update_plan"


TOOL_KIND_TO_NAME: dict[ToolKind, ActionToolName] = {
    ToolKind.SLASH: ActionToolName.SLASH_INVOKE,
    ToolKind.SHELL: ActionToolName.SHELL_RUN,
    ToolKind.INVESTIGATION: ActionToolName.INVESTIGATION_START,
    ToolKind.ALERT: ActionToolName.ALERT_SAMPLE,
    ToolKind.SAMPLE_ALERT: ActionToolName.ALERT_SAMPLE,
    ToolKind.SYNTHETIC_TEST: ActionToolName.SYNTHETIC_RUN,
    ToolKind.TASK_CANCEL: ActionToolName.TASK_CANCEL,
    ToolKind.CLI_COMMAND: ActionToolName.CLI_EXEC,
    ToolKind.IMPLEMENTATION: ActionToolName.CODE_IMPLEMENT,
    ToolKind.LLM_PROVIDER: ActionToolName.LLM_SET_PROVIDER,
    ToolKind.SESSION_GOAL: ActionToolName.SESSION_GOAL_SET,
}

__all__ = ["ActionToolName", "TOOL_KIND_TO_NAME", "ToolKind"]
