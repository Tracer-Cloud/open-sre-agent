"""Discord ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.discord`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _DiscordReportDeliveryAdapter:
    """Discord delivery adapter — posts to a channel or thread when credentials are set."""

    name = "discord"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        discord_creds = resolved.get("discord") if isinstance(resolved, dict) else None
        if not discord_creds:
            logger.debug("[publish] discord delivery: no discord integration configured")
            return False

        discord_ctx = state.get("discord_context") or {}
        bot_token = discord_ctx.get("bot_token") or discord_creds.get("bot_token", "")
        channel_id = discord_ctx.get("channel_id") or discord_creds.get("default_channel_id", "")
        thread_id = discord_ctx.get("thread_id", "")
        logger.debug(
            "[publish] discord delivery: channel_id=%s thread_id=%s auth_configured=%s",
            channel_id,
            thread_id,
            bool(bot_token),
        )
        if not (bot_token and channel_id):
            logger.debug(
                "[publish] discord delivery: skipped - auth_configured=%s channel_id=%s",
                bool(bot_token),
                channel_id,
            )
            return False

        from integrations.discord.delivery import send_discord_report

        posted, error = send_discord_report(
            messages.get("slack_text", ""),
            {"bot_token": bot_token, "channel_id": channel_id, "thread_id": thread_id},
        )
        logger.debug("[publish] discord delivery: posted=%s error=%s", posted, error)
        if not posted:
            logger.warning(
                "[publish] Discord delivery failed: channel=%s error=%s",
                channel_id,
                error,
            )
        return True


discord_delivery_adapter = _DiscordReportDeliveryAdapter()
register_delivery_adapter(discord_delivery_adapter)

__all__ = ["discord_delivery_adapter"]
