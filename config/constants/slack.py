"""Slack environment variable names."""

from __future__ import annotations

SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
SLACK_APP_TOKEN_ENV = "SLACK_APP_TOKEN"
# Comma-separated Slack team ids allowed to fall back to the silo organization
# when they have no install record. When set, any other team is refused instead
# of being silently attributed to the silo org (and its stored credentials).
# Unset preserves the permissive dogfood behaviour (with a warning).
SLACK_SILO_TEAM_IDS_ENV = "OPENSRE_SILO_TEAM_IDS"

# File hosts we may fetch with the bot token. ``url_private`` points at
# ``files.slack.com``; downloads redirect within Slack's own domains, and the
# suffix match covers those. A hop anywhere else is rejected before opening a
# connection.
SLACK_FILE_HOST_SUFFIXES: tuple[str, ...] = (
    "slack.com",
    "slack-files.com",
    "slack-edge.com",
)

__all__ = [
    "SLACK_APP_TOKEN_ENV",
    "SLACK_BOT_TOKEN_ENV",
    "SLACK_FILE_HOST_SUFFIXES",
    "SLACK_SILO_TEAM_IDS_ENV",
]
