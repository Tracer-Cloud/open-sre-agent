"""WhatsApp integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations.config_models import WhatsAppConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any],
    record_id: str,  # noqa: ARG001
) -> tuple[WhatsAppConfig | None, str | None]:
    try:
        cfg = WhatsAppConfig.model_validate(
            {
                "account_sid": credentials.get("account_sid", ""),
                "auth_token": credentials.get("auth_token", ""),
                "from_number": credentials.get("from_number", ""),
                "default_to": credentials.get("default_to"),
            }
        )
    except Exception:
        return None, None
    return cfg, "whatsapp"
