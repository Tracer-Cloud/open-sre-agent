"""Credential resolution and transport dispatch for Buzz messages."""

from __future__ import annotations

from typing import Any

from integrations.buzz.delivery import post_buzz_message
from integrations.buzz.tools.buzz_send_message_tool.models import BuzzDeliveryTarget


def _resolved_config() -> dict[str, Any]:
    from integrations.catalog import resolve_effective_integrations

    entry = resolve_effective_integrations().get("buzz") or {}
    config = entry.get("config") if isinstance(entry, dict) else {}
    return config if isinstance(config, dict) else {}


def resolve_target(channel: str) -> tuple[BuzzDeliveryTarget | None, str]:
    """Resolve the delivery destination from the configured integration.

    Explicit ``channel`` (a channel UUID) targets that channel directly; the
    configured ``default_channel`` is the fallback.
    """
    config = _resolved_config()
    if not config:
        return None, "Buzz is not configured."

    private_key = str(config.get("private_key") or "")
    if not private_key:
        return None, "Buzz is not configured."

    resolved_channel = channel or str(config.get("default_channel") or "")
    if not resolved_channel:
        return None, (
            "No channel to deliver to: pass a channel UUID or configure a "
            "default_channel (BUZZ_DEFAULT_CHANNEL)."
        )

    return (
        BuzzDeliveryTarget(
            relay_url=str(config.get("relay_url") or "http://localhost:3000"),
            private_key=private_key,
            auth_tag=str(config.get("auth_tag") or ""),
            buzz_path=str(config.get("buzz_path") or "buzz"),
            channel=resolved_channel,
        ),
        "",
    )


def dispatch_message(message: str, target: BuzzDeliveryTarget) -> tuple[bool, str]:
    ok, error, _event_id = post_buzz_message(
        target.relay_url,
        target.channel,
        message,
        target.private_key,
        auth_tag=target.auth_tag,
        buzz_path=target.buzz_path,
    )
    return ok, error
