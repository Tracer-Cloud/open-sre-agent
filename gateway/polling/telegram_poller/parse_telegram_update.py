"""Parse Telegram Bot API update payloads into gateway message events."""

from __future__ import annotations

from typing import Any

from gateway.config.get_gateway_settings import TelegramInboundMessage


def parse_update(update: dict[str, Any]) -> TelegramInboundMessage | None:
    """Extract a normalized inbound event from a Telegram update object."""
    if update.get("callback_query"):
        return None

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return None
    from_user = message.get("from") or {}
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    user_id = str(from_user.get("id") or "")
    chat_id = str(chat.get("id") or user_id)
    return TelegramInboundMessage(
        update_id=int(update.get("update_id") or 0),
        user_id=user_id,
        chat_id=chat_id,
        message_id=str(message.get("message_id") or ""),
        text=text.strip(),
    )
