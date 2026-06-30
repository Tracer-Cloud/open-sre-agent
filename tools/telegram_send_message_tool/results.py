"""Stable result shapes for Telegram message delivery."""

from __future__ import annotations

from typing import Any

from tools.telegram_send_message_tool.constants import SOURCE
from tools.telegram_send_message_tool.models import TelegramDeliveryTarget


def failed_result(
    *,
    available: bool,
    error: str,
    error_type: str,
    chat_id: str = "",
    reply_to_message_id: str = "",
    message_length: int = 0,
) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": available,
        "status": "failed",
        "sent": False,
        "error": error,
        "error_type": error_type,
        "chat_id": chat_id,
        "reply_to_message_id": reply_to_message_id,
        "message_length": message_length,
    }


def sent_result(*, target: TelegramDeliveryTarget, message_length: int) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": True,
        "status": "sent",
        "sent": True,
        "error": "",
        "error_type": "",
        "chat_id": target.chat_id,
        "reply_to_message_id": target.reply_to_message_id,
        "message_length": message_length,
    }
