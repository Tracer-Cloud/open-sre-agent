"""Agent-callable Slack workspace roster."""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.web_client import (
    bot_token_configured,
    fetch_team_members,
    resolve_bot_token,
)


class SlackListTeamMembersTool(BaseTool):
    """List the members of the Slack workspace the bot is installed in."""

    name = "slack_list_team_members"
    source = SOURCE
    description = (
        "List members of the Slack *workspace* the bot is installed in "
        "(id, username, real name, title, bot flag) via users.list. "
        "This is the ONLY tool for who is on the team / team roster / member IDs. "
        "Do NOT use slack_read_messages for these questions — channel history is "
        "not a roster, even when a [Slack channel_id=…] context line is present."
    )
    use_cases = [
        'Answering "who is on the team?", "who\'s on the team?", or "list team members"',
        "Resolving a teammate's Slack member ID (U…) from their name for mentions or allowlists",
        "Choosing whom to notify / page about an incident (workspace roster, not channel chat)",
    ]
    anti_examples = [
        "Reading or summarizing a channel/thread (use slack_read_messages)",
        "Inferring teammates from recent channel messages instead of the workspace roster",
        "Looking up users in a different workspace",
    ]
    requires = ["slack"]
    side_effect_level = "read_only"
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "include_bots": {
                "type": "boolean",
                "description": "Include bot users in the roster (default false).",
            },
        },
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' on success, 'failed' otherwise",
        "members": "list of {id, username, real_name, display_name, title, is_bot}",
        "member_count": "number of members returned",
        "truncated": "true when the roster hit the page cap and may be incomplete",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: configuration_error or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(self, include_bots: bool = False, **_kwargs: Any) -> dict[str, Any]:
        target, resolution_error = resolve_bot_token()
        if target is None:
            return tool_unavailable(
                SOURCE,
                resolution_error,
                status="failed",
                error_type="configuration_error",
                members=[],
                member_count=0,
                truncated=False,
            )

        members, error, truncated = fetch_team_members(target)
        if members is None:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": error,
                "error_type": "api_error",
                "members": [],
                "member_count": 0,
                "truncated": False,
            }
        if not include_bots:
            members = [m for m in members if not m["is_bot"]]
        return {
            "source": SOURCE,
            "available": True,
            "status": "read",
            "members": members,
            "member_count": len(members),
            "truncated": truncated,
        }


slack_list_team_members = tool(
    SlackListTeamMembersTool(),
    surfaces=("investigation", "chat", "action"),
)
