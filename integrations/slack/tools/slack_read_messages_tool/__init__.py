"""Registry entrypoint for the Slack read-messages tool."""

from __future__ import annotations

from integrations.slack.tools.slack_read_messages_tool.tool import (
    SlackReadMessagesTool,
    slack_read_messages,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackReadMessagesTool", "slack_read_messages"]
