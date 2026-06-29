"""Long-polling transport for local Telegram gateway development."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from gateway.config import TelegramInboundMessage
from gateway.platforms.telegram.webhook import parse_update

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/getUpdates"


class TelegramPoller:
    """Poll Telegram getUpdates and yield normalized inbound messages."""

    def __init__(self, bot_token: str, *, timeout: int = 30) -> None:
        self._token = bot_token
        self._timeout = timeout
        self._offset = 0

    def poll_once(self) -> list[TelegramInboundMessage]:
        url = _API.format(token=self._token)
        params: dict[str, str | int | list[str]] = {
            "timeout": self._timeout,
            "offset": self._offset + 1,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            response = httpx.get(url, params=params, timeout=float(self._timeout + 5))
            data = response.json() if response.status_code == 200 else {}
        except Exception as exc:
            logger.warning("[telegram-gateway] getUpdates failed: %s", exc)
            time.sleep(2)
            return []
        if not isinstance(data, dict) or not data.get("ok"):
            logger.warning("[telegram-gateway] getUpdates not ok: %s", data)
            time.sleep(2)
            return []
        result = data.get("result")
        if not isinstance(result, list):
            return []

        events: list[TelegramInboundMessage] = []
        for raw in result:
            if not isinstance(raw, dict):
                continue
            update_id = int(raw.get("update_id") or 0)
            self._offset = max(self._offset, update_id)
            parsed = parse_update(raw)
            if parsed is not None:
                events.append(parsed)
        return events

    def run_forever(self, handler: Any) -> None:
        logger.info("[telegram-gateway] starting long-poll loop")
        while True:
            for event in self.poll_once():
                handler(event)
