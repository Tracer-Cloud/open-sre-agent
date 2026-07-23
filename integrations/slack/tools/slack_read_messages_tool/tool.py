"""Agent-callable Slack channel-history / thread read."""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.slack.tools.slack_read_messages_tool.constants import (
    DEFAULT_MESSAGE_LIMIT,
    SOURCE,
)
from integrations.slack.tools.slack_read_messages_tool.results import failed_result, read_result
from integrations.slack.tools.slack_read_messages_tool.validation import (
    clamp_limit,
    validate_channel_id,
)
from integrations.slack.web_client import (
    bot_token_configured,
    fetch_channel_messages,
    resolve_bot_token,
    resolve_channel_id,
)


class SlackReadMessagesTool(BaseTool):
    """Read recent messages from a Slack channel or thread the bot can see."""

    name = "slack_read_messages"
    source = SOURCE
    description = (
        "Read recent *messages* from one Slack channel or thread the bot can see "
        "(conversations.history / conversations.replies). Pass channel ID (C…) or "
        "#channel-name; optional thread_ts for a thread. Returns message text + user "
        'ids — NOT a workspace member roster. For "who is on the team?" / roster / '
        "member IDs use slack_list_team_members instead (do not invent a roster from "
        "channel chat, even when [Slack channel_id=…] is in the user message)."
    )
    use_cases = [
        "Reading recent discussion in an incident channel for context",
        "Reading a specific thread under a parent message ts",
        "Summarizing what was said in a named #channel or 'this channel/thread'",
    ]
    anti_examples = [
        (
            'Answering "who is on the team?" or "list team members" — use '
            + "slack_list_team_members, never substitute channel history"
        ),
        "Building a people/roster list from speakers in channel messages",
        "Searching messages across the whole workspace (use slack_search_messages)",
        "Reading a channel the bot has not been invited to",
    ]
    requires = ["slack"]
    side_effect_level = "read_only"
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": ("Slack channel ID (C…/D…/G…) or #channel-name the bot is in."),
            },
            "limit": {
                "type": "integer",
                "description": "How many recent messages to fetch (1-100, default 50).",
            },
            "thread_ts": {
                "type": "string",
                "description": (
                    "OMIT for 'this channel' / 'summarize the channel' — leaving it "
                    "unset reads whole-channel history. Set it ONLY when the user "
                    "explicitly asks about 'this thread' or a specific thread; it "
                    "then reads just that thread's replies. Never copy the triggering "
                    "message ts here by default — that returns only the current "
                    "(usually empty) thread."
                ),
            },
        },
        "required": ["channel_id"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' on success, 'failed' otherwise",
        "channel_id": "resolved Slack channel ID that was read",
        "messages": "list of {user, ts, thread_ts, text}, oldest first",
        "message_count": "number of messages returned",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(
        self,
        channel_id: str,
        limit: int = DEFAULT_MESSAGE_LIMIT,
        thread_ts: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        valid, normalized_ref, validation_error = validate_channel_id(channel_id)
        if not valid:
            return failed_result(
                available=True,
                error=validation_error,
                error_type="validation_error",
            )

        target, resolution_error = resolve_bot_token()
        if target is None:
            return failed_result(
                available=False,
                error=resolution_error,
                error_type="configuration_error",
            )

        resolved_id, resolve_error = resolve_channel_id(target, normalized_ref)
        if resolved_id is None:
            return failed_result(
                available=True,
                error=resolve_error,
                error_type="api_error" if normalized_ref.startswith("#") else "validation_error",
            )

        messages, error = fetch_channel_messages(
            target,
            channel_id=resolved_id,
            limit=clamp_limit(limit),
            thread_ts=str(thread_ts or "").strip(),
        )
        if messages is None:
            return failed_result(available=True, error=error, error_type="api_error")
        return read_result(channel_id=resolved_id, messages=messages)


slack_read_messages = tool(
    SlackReadMessagesTool(),
    surfaces=("investigation", "chat", "action"),
)
