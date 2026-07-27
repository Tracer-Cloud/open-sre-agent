"""Registry entrypoint for slack_join_channel."""

from __future__ import annotations

from integrations.slack.tools.slack_join_channel_tool.tool import (
    SlackJoinChannelTool,
    slack_join_channel,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackJoinChannelTool", "slack_join_channel"]
