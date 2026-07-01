"""Telegram Bot API client (httpx, outbound + gateway edits)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from platform.notifications.delivery_transport import post_json

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramBotClient:
    """Minimal Telegram Bot API wrapper for gateway operations."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token

    def _call(self, method: str, payload: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
        response = post_json(
            url=_API_BASE.format(token=self._token, method=method),
            payload=payload,
        )
        if not response.ok:
            return False, {}, response.error
        if response.status_code != 200 or not isinstance(response.data, Mapping):
            return False, {}, response.text or f"HTTP {response.status_code}"
        if not response.data.get("ok"):
            description = str(response.data.get("description", "unknown"))
            return False, dict(response.data), description
        result = response.data.get("result")
        return True, dict(result) if isinstance(result, Mapping) else {}, ""

    def send_message(self, chat_id: str, text: str) -> tuple[bool, str, str]:
        ok, result, error = self._call("sendMessage", {"chat_id": chat_id, "text": text})
        if not ok:
            logger.warning("[telegram-gateway] sendMessage failed: %s", error)
            return False, error, ""
        return True, "", str(result.get("message_id") or "")

    def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> tuple[bool, str]:
        ok, _, error = self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": int(message_id), "text": text},
        )
        if not ok:
            logger.debug("[telegram-gateway] editMessageText failed: %s", error)
        return ok, error

    def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {})
