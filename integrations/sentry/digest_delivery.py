"""Delivery readiness checks for scheduled Sentry digest tasks."""

from __future__ import annotations

from platform.scheduler.credentials import resolve_slack_credentials, resolve_telegram_credentials
from platform.scheduler.types import Provider


def telegram_delivery_ready() -> bool:
    """Return True when Telegram bot credentials are available."""
    return bool(resolve_telegram_credentials({}).get("bot_token"))


def slack_delivery_ready() -> bool:
    """Return True when Slack webhook or bot token credentials are available."""
    creds = resolve_slack_credentials({})
    return bool(creds.get("webhook_url") or creds.get("access_token"))


def delivery_provider_ready(provider: Provider | str) -> bool:
    """Return True when ``provider`` can deliver scheduled digest messages."""
    name = provider.value if isinstance(provider, Provider) else str(provider).strip().lower()
    if name == Provider.TELEGRAM.value:
        return telegram_delivery_ready()
    if name == Provider.SLACK.value:
        return slack_delivery_ready()
    return False


def any_digest_delivery_ready() -> bool:
    """Return True when at least one supported digest delivery provider is configured."""
    return telegram_delivery_ready() or slack_delivery_ready()


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
    return (
        "No digest delivery channel is configured. Connect Telegram or Slack first "
        "(`opensre integrations setup telegram` or `opensre integrations setup slack`)."
    )


__all__ = [
    "any_digest_delivery_ready",
    "delivery_provider_ready",
    "digest_delivery_setup_hint",
    "slack_delivery_ready",
    "telegram_delivery_ready",
]
