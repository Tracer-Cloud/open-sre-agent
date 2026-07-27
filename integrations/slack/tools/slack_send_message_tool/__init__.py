"""Registry entrypoint for the Slack send-message tool."""

from __future__ import annotations

from integrations.slack.tools.slack_send_message_tool.tool import (
    SlackSendMessageTool,
    slack_send_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackSendMessageTool", "slack_send_message"]
