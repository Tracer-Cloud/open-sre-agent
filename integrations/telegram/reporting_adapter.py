"""Telegram ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.telegram`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _TelegramReportDeliveryAdapter:
    """Telegram delivery adapter — replies in-thread when credentials are set."""

    name = "telegram"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        telegram_creds = resolved.get("telegram") if isinstance(resolved, dict) else None
        if not telegram_creds:
            logger.debug("[publish] telegram delivery: no telegram integration configured")
            return False

        telegram_ctx = state.get("telegram_context") or {}
        bot_token = telegram_ctx.get("bot_token") or telegram_creds.get("bot_token", "")
        chat_id = telegram_ctx.get("chat_id") or telegram_creds.get("default_chat_id", "")
        reply_to = str(telegram_ctx.get("reply_to_message_id") or "")
        logger.debug(
            "[publish] telegram delivery: chat_id=%s reply_to=%s auth_configured=%s",
            chat_id,
            reply_to,
            bool(bot_token),
        )
        if not (bot_token and chat_id):
            logger.debug(
                "[publish] telegram delivery: skipped - auth_configured=%s chat_id=%s",
                bool(bot_token),
                chat_id,
            )
            return False

        from integrations.telegram.delivery import send_telegram_report

        posted, error = send_telegram_report(
            messages.get("telegram_html", ""),
            {"bot_token": bot_token, "chat_id": chat_id, "reply_to_message_id": reply_to},
        )
        logger.debug("[publish] telegram delivery: posted=%s error=%s", posted, error)
        if not posted:
            logger.warning(
                "[publish] Telegram delivery failed: chat_id=%s error=%s",
                chat_id,
                error,
            )
        return True


telegram_delivery_adapter = _TelegramReportDeliveryAdapter()
register_delivery_adapter(telegram_delivery_adapter)

__all__ = ["telegram_delivery_adapter"]
