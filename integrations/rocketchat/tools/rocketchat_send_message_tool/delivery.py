"""Credential resolution and transport dispatch for Rocket.Chat messages."""

from __future__ import annotations

from typing import Any

from integrations.rocketchat.delivery import (
    post_rocketchat_message,
    post_rocketchat_webhook,
)
from integrations.rocketchat.tools.rocketchat_send_message_tool.models import (
    RocketChatDeliveryTarget,
)


def _resolved_config() -> dict[str, Any]:
    from integrations.catalog import resolve_effective_integrations

    entry = resolve_effective_integrations().get("rocketchat") or {}
    config = entry.get("config") if isinstance(entry, dict) else {}
    return config if isinstance(config, dict) else {}


def resolve_target(channel: str) -> tuple[RocketChatDeliveryTarget | None, str]:
    """Resolve the delivery destination from the configured integration.

    Explicit ``channel`` (or the configured ``default_channel``) targets the
    token-mode REST API. The incoming webhook is the fallback when token
    credentials are absent — its destination is fixed at webhook-creation
    time, so an explicit channel cannot be honored in webhook-only setups.
    """
    config = _resolved_config()
    if not config:
        return None, "Rocket.Chat is not configured."

    server_url = str(config.get("server_url") or "")
    auth_token = str(config.get("auth_token") or "")
    user_id = str(config.get("user_id") or "")
    webhook_url = str(config.get("webhook_url") or "")
    resolved_channel = channel or str(config.get("default_channel") or "")
    has_pat = bool(server_url and auth_token and user_id)

    if has_pat:
        if resolved_channel:
            return (
                RocketChatDeliveryTarget(
                    mode="token",
                    server_url=server_url,
                    auth_token=auth_token,
                    user_id=user_id,
                    channel=resolved_channel,
                ),
                "",
            )
        # Never fall back to the webhook here: its destination was chosen at
        # webhook-creation time and may not be where the caller expects a
        # channel-less send to land.
        return None, (
            "No channel to deliver to: pass a channel or configure a default_channel "
            "(ROCKETCHAT_DEFAULT_CHANNEL)."
        )
    if webhook_url:
        if channel:
            return None, (
                "An explicit channel needs token credentials (server_url, auth_token, "
                "user_id); the configured incoming webhook delivers to a fixed "
                "destination chosen when the webhook was created."
            )
        return RocketChatDeliveryTarget(mode="webhook", webhook_url=webhook_url), ""
    return None, "Rocket.Chat is not configured."


def dispatch_message(message: str, target: RocketChatDeliveryTarget) -> tuple[bool, str]:
    if target.mode == "webhook":
        return post_rocketchat_webhook(target.webhook_url, message)
    ok, error, _message_id = post_rocketchat_message(
        target.server_url,
        target.channel,
        message,
        target.auth_token,
        target.user_id,
    )
    return ok, error
