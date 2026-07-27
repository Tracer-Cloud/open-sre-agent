"""Public Slack integration package.

Outbound delivery and bot-token Web API helpers for agent tools.
"""

from __future__ import annotations

from integrations.slack.classify import classify
from integrations.slack.web_client import (
    SlackBotTarget,
    add_reaction,
    bot_token_configured,
    fetch_channel_messages,
    fetch_team_members,
    join_channel,
    normalize_channel_ref,
    post_channel_message,
    remove_reaction,
    resolve_bot_token,
    resolve_channel_id,
    search_messages,
)

__all__ = [
    "SlackBotTarget",
    "add_reaction",
    "bot_token_configured",
    "classify",
    "fetch_channel_messages",
    "fetch_team_members",
    "join_channel",
    "normalize_channel_ref",
    "post_channel_message",
    "remove_reaction",
    "resolve_bot_token",
    "resolve_channel_id",
    "search_messages",
]
