"""What Mattermost needs before it is considered configured.

Mattermost can be reached two ways, and setup accepts either: an incoming
webhook URL (a fixed destination), or a personal access token — the server
URL and auth token together — which lets delivery target channels
dynamically. Neither field is individually required; the requirement is that
*one of the two paths* is complete. Every field is therefore optional here, and
:func:`integrations.mattermost.verifier.verify_mattermost` enforces the real
rule — it already rejects an incomplete pair, and keeping the check there means
setup and health checks agree on what "configured" means.

The token is mirrored to the keyring / ``.env`` so the deploy preflight (which
reads the environment, not the store) sees a Mattermost that was configured
through ``integrations setup``. The webhook URL is the exception: it embeds
its own secret, so — like ``SLACK_WEBHOOK_URL`` — it stays store-only.
"""

from __future__ import annotations

from config.constants.mattermost import (
    MATTERMOST_AUTH_TOKEN_ENV,
    MATTERMOST_DEFAULT_CHANNEL_ENV,
    MATTERMOST_SERVER_URL_ENV,
)
from integrations.mattermost.verifier import verify_mattermost
from integrations.setup_flow import IntegrationSetupSpec, SetupField

SERVER_URL_FIELD = "server_url"
AUTH_TOKEN_FIELD = "auth_token"
WEBHOOK_URL_FIELD = "webhook_url"
DEFAULT_CHANNEL_FIELD = "default_channel"

MATTERMOST_SETUP = IntegrationSetupSpec(
    service="mattermost",
    fields=(
        SetupField(
            name=SERVER_URL_FIELD,
            label="Mattermost server URL",
            prompt="Mattermost server URL (e.g. https://chat.example.com)",
            env_var=MATTERMOST_SERVER_URL_ENV,
            required=False,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Mattermost personal access token",
            prompt="Mattermost personal access token (blank for webhook-only)",
            env_var=MATTERMOST_AUTH_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=WEBHOOK_URL_FIELD,
            label="Mattermost incoming webhook URL",
            prompt="Mattermost incoming webhook URL (blank for token setup)",
            # Store-only: the URL embeds its secret, so it is not mirrored to
            # .env (like SLACK_WEBHOOK_URL).
            required=False,
            secret=True,
        ),
        SetupField(
            name=DEFAULT_CHANNEL_FIELD,
            label="Default channel",
            prompt="Default channel id (optional)",
            env_var=MATTERMOST_DEFAULT_CHANNEL_ENV,
            required=False,
        ),
    ),
    verify=verify_mattermost,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "DEFAULT_CHANNEL_FIELD",
    "MATTERMOST_SETUP",
    "SERVER_URL_FIELD",
    "WEBHOOK_URL_FIELD",
]
