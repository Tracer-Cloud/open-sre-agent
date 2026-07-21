"""Agent-callable Slack workspace message search."""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.web_client import bot_token_configured, resolve_bot_token, search_messages


class SlackSearchMessagesTool(BaseTool):
    """Search Slack messages across the workspace."""

    name = "slack_search_messages"
    source = SOURCE
    description = (
        "Search Slack *messages* workspace-wide (search.messages) using the bot token. "
        "Use Slack search syntax (e.g. 'in:#incidents timeout', 'from:@user error'). "
        "Requires the search:read bot scope. Not for workspace roster — use "
        "slack_list_team_members for who is on the team / member IDs."
    )
    use_cases = [
        "Finding prior discussion of an incident keyword",
        "Locating where a bug was reported in Slack",
    ]
    anti_examples = [
        'Answering "who is on the team?" (use slack_list_team_members)',
        "Reading one known channel's recent history (use slack_read_messages)",
        "Searching without a concrete query",
    ]
    requires = ["slack"]
    side_effect_level = "read_only"
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Slack search query string.",
            },
            "count": {
                "type": "integer",
                "description": "Max matches to return (1-100, default 20).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' on success, 'failed' otherwise",
        "matches": "list of {channel_id, user, ts, text, permalink}",
        "match_count": "number of matches returned",
        "error": "error detail when status is 'failed'",
        "error_type": "validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(self, query: str, count: int = 20, **_kwargs: Any) -> dict[str, Any]:
        target, resolution_error = resolve_bot_token()
        if target is None:
            return tool_unavailable(
                SOURCE,
                resolution_error,
                status="failed",
                error_type="configuration_error",
                matches=[],
                match_count=0,
            )

        matches, error = search_messages(target, query=query, count=count)
        if matches is None:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": error,
                "error_type": ("validation_error" if "empty" in error else "api_error"),
                "matches": [],
                "match_count": 0,
            }
        return {
            "source": SOURCE,
            "available": True,
            "status": "read",
            "matches": matches,
            "match_count": len(matches),
        }


slack_search_messages = tool(
    SlackSearchMessagesTool(),
    surfaces=("investigation", "chat", "action"),
)
