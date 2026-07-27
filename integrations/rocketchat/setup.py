"""What Rocket.Chat needs before it is considered configured.

Rocket.Chat can be reached two ways, and setup accepts either: an incoming
webhook URL (a fixed destination), or a personal access token — the server URL,
auth token, and user id together — which lets delivery target channels
dynamically. Neither field is individually required; the requirement is that
*one of the two paths* is complete. Every field is therefore optional here, and
:func:`integrations.rocketchat.verifier.verify_rocketchat` enforces the real
rule — it already rejects an incomplete pair, and keeping the check there means
setup and health checks agree on what "configured" means.

The token trio is mirrored to the keyring / ``.env`` so the deploy preflight
(which reads the environment, not the store) sees a Rocket.Chat that was
configured through ``integrations setup``. The webhook URL is the exception: it
embeds its own secret, so — like ``SLACK_WEBHOOK_URL`` — it stays store-only.
"""

from __future__ import annotations

from config.constants.rocketchat import (
    ROCKETCHAT_AUTH_TOKEN_ENV,
    ROCKETCHAT_DEFAULT_CHANNEL_ENV,
    ROCKETCHAT_SERVER_URL_ENV,
    ROCKETCHAT_USER_ID_ENV,
)
from integrations.rocketchat.verifier import verify_rocketchat
from integrations.setup_flow import IntegrationSetupSpec, SetupField

SERVER_URL_FIELD = "server_url"
AUTH_TOKEN_FIELD = "auth_token"
USER_ID_FIELD = "user_id"
WEBHOOK_URL_FIELD = "webhook_url"
DEFAULT_CHANNEL_FIELD = "default_channel"

ROCKETCHAT_SETUP = IntegrationSetupSpec(
    service="rocketchat",
    fields=(
        SetupField(
            name=SERVER_URL_FIELD,
            label="Rocket.Chat server URL",
            prompt="Rocket.Chat server URL (e.g. https://chat.example.com)",
            env_var=ROCKETCHAT_SERVER_URL_ENV,
            required=False,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Rocket.Chat personal access token",
            prompt="Rocket.Chat personal access token (blank for webhook-only)",
            env_var=ROCKETCHAT_AUTH_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=USER_ID_FIELD,
            label="Rocket.Chat user ID",
            prompt="Rocket.Chat user ID (blank for webhook-only)",
            env_var=ROCKETCHAT_USER_ID_ENV,
            required=False,
        ),
        SetupField(
            name=WEBHOOK_URL_FIELD,
            label="Rocket.Chat incoming webhook URL",
            prompt="Rocket.Chat incoming webhook URL (blank for token setup)",
            # Store-only: the URL embeds its secret, so it is not mirrored to
            # .env (like SLACK_WEBHOOK_URL).
            required=False,
            secret=True,
        ),
        SetupField(
            name=DEFAULT_CHANNEL_FIELD,
            label="Default channel",
            prompt="Default channel (e.g. #incidents, optional)",
            env_var=ROCKETCHAT_DEFAULT_CHANNEL_ENV,
            required=False,
        ),
    ),
    verify=verify_rocketchat,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "DEFAULT_CHANNEL_FIELD",
    "ROCKETCHAT_SETUP",
    "SERVER_URL_FIELD",
    "USER_ID_FIELD",
    "WEBHOOK_URL_FIELD",
]
