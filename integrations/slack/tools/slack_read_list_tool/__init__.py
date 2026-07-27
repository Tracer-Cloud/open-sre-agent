"""Registry entrypoint for the Slack Lists read tool."""

from __future__ import annotations

from integrations.slack.tools.slack_read_list_tool.tool import SlackReadListTool, slack_read_list

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackReadListTool", "slack_read_list"]
