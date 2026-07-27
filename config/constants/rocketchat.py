"""Rocket.Chat environment variable names.

``ROCKETCHAT_WEBHOOK_URL`` is intentionally absent: the webhook URL embeds its
own secret, so — like ``SLACK_WEBHOOK_URL`` — it is kept in the integration
store only and never written to ``.env``.
"""

from __future__ import annotations

ROCKETCHAT_SERVER_URL_ENV = "ROCKETCHAT_SERVER_URL"
ROCKETCHAT_AUTH_TOKEN_ENV = "ROCKETCHAT_AUTH_TOKEN"
ROCKETCHAT_USER_ID_ENV = "ROCKETCHAT_USER_ID"
ROCKETCHAT_DEFAULT_CHANNEL_ENV = "ROCKETCHAT_DEFAULT_CHANNEL"

__all__ = [
    "ROCKETCHAT_AUTH_TOKEN_ENV",
    "ROCKETCHAT_DEFAULT_CHANNEL_ENV",
    "ROCKETCHAT_SERVER_URL_ENV",
    "ROCKETCHAT_USER_ID_ENV",
]
