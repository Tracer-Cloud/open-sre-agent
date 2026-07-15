"""Registry entrypoint for the Slack team-members tool."""

from __future__ import annotations

from integrations.slack.tools.slack_list_members_tool.tool import (
    SlackListTeamMembersTool,
    slack_list_team_members,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackListTeamMembersTool", "slack_list_team_members"]
