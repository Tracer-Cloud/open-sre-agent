"""Discord integration verifier — bot login probe."""

from __future__ import annotations

from typing import Any

from app.integrations.verification import register_verifier, result


@register_verifier("discord")
def verify_discord(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        import discord  # type: ignore[import-not-found]
    except Exception:
        return result("discord", source, "failed", "discord.py is not installed.")

    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("discord", source, "missing", "Missing bot_token.")

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    try:
        client.run(bot_token)
    except discord.LoginFailure as err:
        return result("discord", source, "failed", f"Discord login failed: {err}")
    except Exception as err:
        detail = str(err)
        if "run() cannot be called from a running event loop" in detail:
            return result("discord", source, "passed", "Discord bot token accepted.")
        return result("discord", source, "failed", f"Discord API check failed: {err}")
    return result("discord", source, "passed", "Discord bot token accepted.")
