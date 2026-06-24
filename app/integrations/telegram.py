"""Telegram integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import TelegramBotConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[TelegramBotConfig | None, str | None]:
    if not (credentials.get("bot_token") or "").strip():
        return None, None
    try:
        cfg = TelegramBotConfig.model_validate(
            {
                "bot_token": credentials.get("bot_token", ""),
                "default_chat_id": credentials.get("default_chat_id"),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="telegram", record_id=record_id)
        return None, None
    return cfg, "telegram"
