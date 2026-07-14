"""Registry entrypoint for slack_add_reaction."""

from __future__ import annotations

from integrations.slack.tools.slack_add_reaction_tool.tool import (
    SlackAddReactionTool,
    slack_add_reaction,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "SlackAddReactionTool", "slack_add_reaction"]
