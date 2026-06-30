"""Credential resolution and transport dispatch for Telegram messages."""

from __future__ import annotations

from integrations.telegram.credentials import load_credentials_from_env
from integrations.telegram.delivery import send_telegram_report
from integrations.telegram.tools.telegram_send_message_tool.models import TelegramDeliveryTarget


def resolve_target(
    chat_id: str,
    reply_to_message_id: str,
) -> tuple[TelegramDeliveryTarget | None, str]:
    try:
        creds = load_credentials_from_env(chat_id_override=chat_id or None)
    except Exception as exc:
        return None, str(exc)
    return (
        TelegramDeliveryTarget(
            bot_token=creds.bot_token,
            chat_id=creds.chat_id,
            reply_to_message_id=reply_to_message_id,
        ),
        "",
    )


def dispatch_message(message: str, target: TelegramDeliveryTarget) -> tuple[bool, str]:
    return send_telegram_report(
        message,
        {
            "bot_token": target.bot_token,
            "chat_id": target.chat_id,
            "reply_to_message_id": target.reply_to_message_id,
        },
        parse_mode="",
    )
