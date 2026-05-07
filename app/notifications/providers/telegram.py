"""Telegram notification provider implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.notifications.interface import NotificationProvider
from app.utils.telegram_delivery import send_telegram_report, post_telegram_message

logger = logging.getLogger(__name__)


from app.notifications.models import NotificationEvent


class TelegramProvider(NotificationProvider):
    """Telegram notification provider."""

    def send_notification(
        self,
        event: NotificationEvent,
        config: dict[str, Any],
    ) -> tuple[bool, str]:
        """Send an RCA report to Telegram."""
        return send_telegram_report(event.format_text(), config)

    def probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Verify Telegram connectivity."""
        bot_token = str(config.get("bot_token") or "")
        chat_id = str(config.get("default_chat_id") or config.get("chat_id") or "")
        
        if not bot_token or not chat_id:
            return False, "Missing bot_token or chat_id"

        # Try to send a probe message
        success, error, _ = post_telegram_message(
            chat_id=chat_id,
            text="OpenSRE connectivity check",
            bot_token=bot_token,
        )
        return success, error or "Connected"
