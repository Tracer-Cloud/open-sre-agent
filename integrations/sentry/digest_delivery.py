"""Delivery readiness checks for scheduled Sentry digest tasks."""

from __future__ import annotations

from platform.scheduler.credentials import (
    resolve_rocketchat_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
)
from platform.scheduler.types import Provider


def telegram_delivery_ready() -> bool:
    """Return True when Telegram bot credentials are available."""
    return bool(resolve_telegram_credentials({}).get("bot_token"))


def slack_delivery_ready() -> bool:
    """Return True when Slack webhook or bot token credentials are available."""
    creds = resolve_slack_credentials({})
    return bool(creds.get("webhook_url") or creds.get("access_token"))


def rocketchat_delivery_ready() -> bool:
    """Return True when Rocket.Chat token credentials are available.

    Digest tasks always target an explicit ``--chat-id`` destination, which an
    incoming webhook's fixed destination cannot honor — same rule as the
    ``rocketchat`` cron provider — so readiness requires the full token trio,
    not just a configured webhook.
    """
    creds = resolve_rocketchat_credentials({})
    return bool(creds.get("server_url") and creds.get("auth_token") and creds.get("user_id"))


def delivery_provider_ready(provider: Provider | str) -> bool:
    """Return True when ``provider`` can deliver scheduled digest messages."""
    name = provider.value if isinstance(provider, Provider) else str(provider).strip().lower()
    if name == Provider.TELEGRAM.value:
        return telegram_delivery_ready()
    if name == Provider.SLACK.value:
        return slack_delivery_ready()
    if name == Provider.ROCKETCHAT.value:
        return rocketchat_delivery_ready()
    return False


def any_digest_delivery_ready() -> bool:
    """Return True when at least one supported digest delivery provider is configured."""
    return telegram_delivery_ready() or slack_delivery_ready() or rocketchat_delivery_ready()


def digest_delivery_setup_hint(provider: Provider | str | None = None) -> str:
    """Human-readable setup guidance when delivery is not configured."""
    if provider is not None:
        name = provider.value if isinstance(provider, Provider) else str(provider).strip().lower()
        if name == Provider.TELEGRAM.value:
            return (
                "Telegram is not configured for delivery. Run "
                "`opensre integrations setup telegram` or set TELEGRAM_BOT_TOKEN."
            )
        if name == Provider.SLACK.value:
            return (
                "Slack is not configured for delivery. Run "
                "`opensre integrations setup slack` or set SLACK_WEBHOOK_URL / SLACK_BOT_TOKEN."
            )
        if name == Provider.ROCKETCHAT.value:
            return (
                "Rocket.Chat is not configured for delivery with token credentials. Run "
                "`opensre integrations setup rocketchat` or set ROCKETCHAT_SERVER_URL, "
                "ROCKETCHAT_AUTH_TOKEN, and ROCKETCHAT_USER_ID (a webhook alone cannot "
                "target an explicit --chat-id)."
            )
    return (
        "No digest delivery channel is configured. Connect Telegram, Slack, or Rocket.Chat "
        "first (`opensre integrations setup telegram`, `opensre integrations setup slack`, "
        "or `opensre integrations setup rocketchat`)."
    )


__all__ = [
    "any_digest_delivery_ready",
    "delivery_provider_ready",
    "digest_delivery_setup_hint",
    "rocketchat_delivery_ready",
    "slack_delivery_ready",
    "telegram_delivery_ready",
]
