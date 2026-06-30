"""Typed models for Telegram message delivery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramDeliveryTarget:
    """Resolved Telegram delivery destination.

    ``bot_token`` is deliberately excluded from repr so failed assertions,
    tracebacks, or debug logs do not leak the Telegram credential.
    """

    bot_token: str
    chat_id: str
    reply_to_message_id: str = ""

    def __repr__(self) -> str:
        return (
            "TelegramDeliveryTarget("
            f"chat_id={self.chat_id!r}, "
            f"reply_to_message_id={self.reply_to_message_id!r}, "
            "bot_token=<redacted>)"
        )
