"""Registry entrypoint for slack_capture_task."""

from __future__ import annotations

from integrations.slack.tools.slack_capture_task_tool.tool import (
    SlackCaptureTaskTool,
    slack_capture_task,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackCaptureTaskTool", "slack_capture_task"]
