"""Mattermost environment variable names.

``MATTERMOST_WEBHOOK_URL`` is intentionally absent: the webhook URL embeds its
own secret, so — like ``SLACK_WEBHOOK_URL`` — it is kept in the integration
store only and never written to ``.env``.
"""

from __future__ import annotations

MATTERMOST_SERVER_URL_ENV = "MATTERMOST_SERVER_URL"
MATTERMOST_AUTH_TOKEN_ENV = "MATTERMOST_AUTH_TOKEN"
MATTERMOST_DEFAULT_CHANNEL_ENV = "MATTERMOST_DEFAULT_CHANNEL"

__all__ = [
    "MATTERMOST_AUTH_TOKEN_ENV",
    "MATTERMOST_DEFAULT_CHANNEL_ENV",
    "MATTERMOST_SERVER_URL_ENV",
]
