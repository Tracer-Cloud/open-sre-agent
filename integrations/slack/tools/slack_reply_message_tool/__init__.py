"""Registry entrypoint for the Slack reply-message tool."""

from __future__ import annotations

from integrations.slack.tools.slack_reply_message_tool.tool import (
    SlackReplyMessageTool,
    slack_reply_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackReplyMessageTool", "slack_reply_message"]
