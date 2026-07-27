"""Slack environment variable names."""

from __future__ import annotations

SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
SLACK_APP_TOKEN_ENV = "SLACK_APP_TOKEN"
# Comma-separated Slack team ids allowed to fall back to the silo organization
# when they have no install record. When set, any other team is refused instead
# of being silently attributed to the silo org (and its stored credentials).
# Unset preserves the permissive dogfood behaviour (with a warning).
SLACK_SILO_TEAM_IDS_ENV = "OPENSRE_SILO_TEAM_IDS"

__all__ = [
    "SLACK_APP_TOKEN_ENV",
    "SLACK_BOT_TOKEN_ENV",
    "SLACK_SILO_TEAM_IDS_ENV",
]
