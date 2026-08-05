"""Delivery readiness checks for scheduled task messages (provider-generic).

Shared by every scheduled-report feature (Sentry digests, PostHog metric
reports, ...). Lives in ``platform.scheduler`` rather than any single vendor
package so integrations depend downward on it instead of on each other.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from platform.scheduler.credentials import (
    resolve_rocketchat_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
)
from platform.scheduler.types import Provider

_console = Console()


def telegram_delivery_ready() -> bool:
    """Return True when Telegram bot credentials are available."""
    return bool(resolve_telegram_credentials({}).get("bot_token"))


def slack_can_deliver(creds: Mapping[str, Any], *, chat_id: str) -> bool:
    """Return True when Slack ``creds`` can deliver to ``chat_id``.

    An explicit ``chat_id`` requires a bot ``access_token`` — an incoming
    webhook is bound to one channel and ignores ``chat_id``. With an empty
    ``chat_id``, a webhook alone is enough (channel-bound cron style).
    """
    token = str(creds.get("access_token") or "").strip()
    webhook = str(creds.get("webhook_url") or "").strip()
    if chat_id.strip():
        return bool(token)
    return bool(webhook)


def slack_delivery_ready() -> bool:
    """Return True when Slack can deliver chat-id-targeted report schedules.

    Digest/report CLIs always require ``--chat-id``, so readiness demands a
    bot token (same rule as :func:`slack_can_deliver` with a non-empty chat).
    """
    return slack_can_deliver(resolve_slack_credentials({}), chat_id="required")


def rocketchat_delivery_ready() -> bool:
    """Return True when Rocket.Chat token credentials are available.

    Digest tasks always target an explicit ``--chat-id`` destination, which an
    incoming webhook's fixed destination cannot honor — same rule as the
    ``rocketchat`` cron provider — so readiness requires the full token trio,
    not just a configured webhook.
    """
    creds = resolve_rocketchat_credentials({})
    return bool(creds.get("server_url") and creds.get("auth_token") and creds.get("user_id"))


@dataclass(frozen=True, slots=True)
class _DeliveryProviderSpec:
    provider: Provider
    label: str
    ready: Callable[[], bool]
    setup_hint: str


# Discord is a member of Provider (cron delivery supports it) but has no
# readiness/send path here, so it is deliberately omitted rather than exposed
# as a broken choice.
_DELIVERY_SPECS: tuple[_DeliveryProviderSpec, ...] = (
    _DeliveryProviderSpec(
        provider=Provider.TELEGRAM,
        label="Telegram",
        ready=telegram_delivery_ready,
        setup_hint=(
            "Telegram is not configured for delivery. Run "
            "`opensre integrations setup telegram` or set TELEGRAM_BOT_TOKEN."
        ),
    ),
    _DeliveryProviderSpec(
        provider=Provider.SLACK,
        label="Slack",
        ready=slack_delivery_ready,
        setup_hint=(
            "Slack is not configured for delivery with a bot token. Run "
            "`opensre integrations setup slack` or set SLACK_BOT_TOKEN. If a Slack "
            "webhook is configured, remove it: a webhook takes precedence over the "
            "bot token and always posts to the one channel it was created for, so "
            "it cannot honour an explicit --chat-id."
        ),
    ),
    _DeliveryProviderSpec(
        provider=Provider.ROCKETCHAT,
        label="Rocket.Chat",
        ready=rocketchat_delivery_ready,
        setup_hint=(
            "Rocket.Chat is not configured for delivery with token credentials. Run "
            "`opensre integrations setup rocketchat` or set ROCKETCHAT_SERVER_URL, "
            "ROCKETCHAT_AUTH_TOKEN, and ROCKETCHAT_USER_ID (a webhook alone cannot "
            "target an explicit --chat-id)."
        ),
    ),
)

_SPECS_BY_NAME: dict[str, _DeliveryProviderSpec] = {
    spec.provider.value: spec for spec in _DELIVERY_SPECS
}

SUPPORTED_DELIVERY_PROVIDERS: tuple[Provider, ...] = tuple(
    spec.provider for spec in _DELIVERY_SPECS
)

_SUPPORTED_PROVIDER_NAMES = ", ".join(spec.provider.value for spec in _DELIVERY_SPECS)


def _provider_name(provider: Provider | str) -> str:
    if isinstance(provider, Provider):
        return provider.value
    return str(provider).strip().lower()


def _any_setup_hint() -> str:
    labels = [spec.label for spec in _DELIVERY_SPECS]
    if len(labels) == 1:
        joined = labels[0]
    else:
        joined = f"{', '.join(labels[:-1])}, or {labels[-1]}"
    setup_cmds = ", ".join(
        f"`opensre integrations setup {spec.provider.value}`" for spec in _DELIVERY_SPECS
    )
    return f"No delivery channel is configured. Connect {joined} first ({setup_cmds})."


def delivery_provider_ready(provider: Provider | str) -> bool:
    """Return True when ``provider`` can deliver scheduled messages."""
    spec = _SPECS_BY_NAME.get(_provider_name(provider))
    return spec.ready() if spec is not None else False


def any_delivery_ready() -> bool:
    """Return True when at least one supported delivery provider is configured."""
    return any(spec.ready() for spec in _DELIVERY_SPECS)


def delivery_setup_hint(provider: Provider | str | None = None) -> str:
    """Human-readable setup guidance when delivery is not configured."""
    if provider is not None:
        name = _provider_name(provider)
        spec = _SPECS_BY_NAME.get(name)
        if spec is not None:
            return spec.setup_hint
        return (
            f"{name!r} is not a supported delivery provider for scheduled reports. "
            f"Use one of: {_SUPPORTED_PROVIDER_NAMES}."
        )
    return _any_setup_hint()


def require_delivery_provider(provider: Provider | str) -> None:
    """Exit when ``provider`` cannot deliver scheduled report messages."""
    if delivery_provider_ready(provider):
        return
    _console.print(f"[red]{delivery_setup_hint(provider)}[/red]")
    raise SystemExit(1)


__all__ = [
    "SUPPORTED_DELIVERY_PROVIDERS",
    "any_delivery_ready",
    "delivery_provider_ready",
    "delivery_setup_hint",
    "require_delivery_provider",
    "rocketchat_delivery_ready",
    "slack_can_deliver",
    "slack_delivery_ready",
    "telegram_delivery_ready",
]
