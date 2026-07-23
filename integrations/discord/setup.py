"""What Discord needs before it is considered configured.

Only the bot token is required — it is what the send-message tool and the
gateway authenticate with, and the one field the verifier checks. The
application id, public key, and default channel id are needed for specific
features (slash-command registration, interaction verification, a default
delivery target) and are optional at setup time.

Registering the ``/investigate`` slash command has to happen after the app
exists, so it is the spec's ``finalize`` step rather than part of persistence;
it needs the application id, which is therefore validated as required before
the command is pushed.
"""

from __future__ import annotations

from config.constants.discord import (
    DISCORD_APPLICATION_ID_ENV,
    DISCORD_BOT_TOKEN_ENV,
    DISCORD_DEFAULT_CHANNEL_ID_ENV,
    DISCORD_PUBLIC_KEY_ENV,
)
from integrations.discord.slash_command import register_investigate_command
from integrations.discord.verifier import verify_discord
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BOT_TOKEN_FIELD = "bot_token"
APPLICATION_ID_FIELD = "application_id"
PUBLIC_KEY_FIELD = "public_key"
DEFAULT_CHANNEL_ID_FIELD = "default_channel_id"


def _register_slash_command(credentials: dict[str, str | None]) -> str:
    """Register ``/investigate`` when an application id is present."""
    application_id = credentials.get(APPLICATION_ID_FIELD)
    bot_token = credentials.get(BOT_TOKEN_FIELD)
    if not application_id or not bot_token:
        return ""
    return register_investigate_command(application_id, bot_token)


DISCORD_SETUP = IntegrationSetupSpec(
    service="discord",
    fields=(
        SetupField(
            name=BOT_TOKEN_FIELD,
            label="Discord bot token",
            env_var=DISCORD_BOT_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=APPLICATION_ID_FIELD,
            label="Discord application ID",
            env_var=DISCORD_APPLICATION_ID_ENV,
            required=False,
        ),
        SetupField(
            name=PUBLIC_KEY_FIELD,
            label="Discord public key",
            prompt="Discord public key (from the Developer Portal)",
            env_var=DISCORD_PUBLIC_KEY_ENV,
            required=False,
        ),
        SetupField(
            name=DEFAULT_CHANNEL_ID_FIELD,
            label="Default channel ID",
            prompt="Default channel ID (optional)",
            env_var=DISCORD_DEFAULT_CHANNEL_ID_ENV,
            required=False,
        ),
    ),
    verify=verify_discord,
    finalize=_register_slash_command,
)

__all__ = [
    "APPLICATION_ID_FIELD",
    "BOT_TOKEN_FIELD",
    "DEFAULT_CHANNEL_ID_FIELD",
    "DISCORD_SETUP",
    "PUBLIC_KEY_FIELD",
]
