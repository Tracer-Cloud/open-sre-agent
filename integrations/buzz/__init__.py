"""Buzz integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import BuzzConfig

logger = logging.getLogger(__name__)


def classify(credentials: dict[str, Any], record_id: str) -> tuple[BuzzConfig | None, str | None]:
    has_key = (credentials.get("private_key") or "").strip()
    if not has_key:
        return None, None
    try:
        cfg = BuzzConfig.model_validate(
            {
                "relay_url": credentials.get("relay_url") or "http://localhost:3000",
                "private_key": credentials.get("private_key", ""),
                "default_channel": credentials.get("default_channel", ""),
                "auth_tag": credentials.get("auth_tag", ""),
                "buzz_path": credentials.get("buzz_path") or "buzz",
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="buzz", record_id=record_id)
        return None, None
    return cfg, "buzz"
