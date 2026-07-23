"""Rocket.Chat ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.rocketchat`` directly (same layering rule as the other vendor
adapters — T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _RocketChatReportDeliveryAdapter:
    """Rocket.Chat delivery adapter — posts to a channel when credentials are set."""

    name = "rocketchat"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        rocketchat_creds = resolved.get("rocketchat") if isinstance(resolved, dict) else None
        if not rocketchat_creds:
            logger.debug("[publish] rocketchat delivery: no rocketchat integration configured")
            return False

        rocketchat_ctx = state.get("rocketchat_context") or {}
        server_url = rocketchat_ctx.get("server_url") or rocketchat_creds.get("server_url", "")
        auth_token = rocketchat_ctx.get("auth_token") or rocketchat_creds.get("auth_token", "")
        user_id = rocketchat_ctx.get("user_id") or rocketchat_creds.get("user_id", "")
        webhook_url = rocketchat_ctx.get("webhook_url") or rocketchat_creds.get("webhook_url", "")
        channel = rocketchat_ctx.get("channel") or rocketchat_creds.get("default_channel", "")
        pat_ready = bool(server_url and auth_token and user_id and channel)
        logger.debug(
            "[publish] rocketchat delivery: server_url=%s channel=%s auth_configured=%s webhook_configured=%s",
            server_url,
            channel,
            bool(auth_token and user_id),
            bool(webhook_url),
        )
        if not webhook_url and not pat_ready:
            logger.debug(
                "[publish] rocketchat delivery: skipped - auth_configured=%s channel=%s webhook_configured=%s",
                bool(auth_token and user_id),
                channel,
                bool(webhook_url),
            )
            return False

        from integrations.rocketchat.delivery import send_rocketchat_report

        posted, error = send_rocketchat_report(
            messages.get("slack_text", ""),
            {
                "server_url": server_url,
                "auth_token": auth_token,
                "user_id": user_id,
                "webhook_url": webhook_url,
                "channel": channel,
            },
        )
        logger.debug("[publish] rocketchat delivery: posted=%s error=%s", posted, error)
        if not posted:
            destination = channel or ("webhook" if webhook_url else "unknown")
            logger.warning(
                "[publish] Rocket.Chat delivery failed: destination=%s error=%s",
                destination,
                error,
            )
        return True


rocketchat_delivery_adapter = _RocketChatReportDeliveryAdapter()
register_delivery_adapter(rocketchat_delivery_adapter)

__all__ = ["rocketchat_delivery_adapter"]
