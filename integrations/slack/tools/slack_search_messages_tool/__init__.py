"""Registry entrypoint for slack_search_messages."""

from __future__ import annotations

from integrations.slack.tools.slack_search_messages_tool.tool import (
    SlackSearchMessagesTool,
    slack_search_messages,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackSearchMessagesTool", "slack_search_messages"]
