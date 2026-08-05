"""Discord environment variable names and API constants."""

from __future__ import annotations

DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
DISCORD_APPLICATION_ID_ENV = "DISCORD_APPLICATION_ID"
DISCORD_PUBLIC_KEY_ENV = "DISCORD_PUBLIC_KEY"
DISCORD_DEFAULT_CHANNEL_ID_ENV = "DISCORD_DEFAULT_CHANNEL_ID"
DISCORD_ALLOWED_USERS_ENV = "DISCORD_ALLOWED_USERS"
DISCORD_ALLOW_OPEN_GUILD_ENV = "DISCORD_ALLOW_OPEN_GUILD"
# Optional comma-separated guild ids that may use this silo's organization.
# Unset = dogfood (any guild the bot is in inherits the org); set in prod.
DISCORD_SILO_GUILD_IDS_ENV = "DISCORD_SILO_GUILD_IDS"

# Discord's REST API base. The host is fixed SaaS — not user-configurable, unlike
# a self-hosted server URL — so it lives here once rather than being spelled out
# at each call site (verifier, slash-command registration, message delivery).
DISCORD_API_BASE = "https://discord.com/api/v10"

# Attachment CDN hosts we may fetch with the bot token (no open redirects).
DISCORD_ATTACHMENT_HOST_SUFFIXES: tuple[str, ...] = (
    "discordapp.com",
    "discord.com",
    "discordapp.net",
)

__all__ = [
    "DISCORD_API_BASE",
    "DISCORD_APPLICATION_ID_ENV",
    "DISCORD_ATTACHMENT_HOST_SUFFIXES",
    "DISCORD_BOT_TOKEN_ENV",
    "DISCORD_ALLOW_OPEN_GUILD_ENV",
    "DISCORD_ALLOWED_USERS_ENV",
    "DISCORD_DEFAULT_CHANNEL_ID_ENV",
    "DISCORD_PUBLIC_KEY_ENV",
    "DISCORD_SILO_GUILD_IDS_ENV",
]
