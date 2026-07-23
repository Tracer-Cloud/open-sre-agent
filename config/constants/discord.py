"""Discord environment variable names and API constants."""

from __future__ import annotations

DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
DISCORD_APPLICATION_ID_ENV = "DISCORD_APPLICATION_ID"
DISCORD_PUBLIC_KEY_ENV = "DISCORD_PUBLIC_KEY"
DISCORD_DEFAULT_CHANNEL_ID_ENV = "DISCORD_DEFAULT_CHANNEL_ID"

# Discord's REST API base. The host is fixed SaaS — not user-configurable, unlike
# a self-hosted server URL — so it lives here once rather than being spelled out
# at each call site (verifier, slash-command registration, message delivery).
DISCORD_API_BASE = "https://discord.com/api/v10"

__all__ = [
    "DISCORD_API_BASE",
    "DISCORD_APPLICATION_ID_ENV",
    "DISCORD_BOT_TOKEN_ENV",
    "DISCORD_DEFAULT_CHANNEL_ID_ENV",
    "DISCORD_PUBLIC_KEY_ENV",
]
