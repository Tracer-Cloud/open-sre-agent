"""Coralogix integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import CoralogixIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "base_url": credentials.get("base_url", ""),
                "application_name": credentials.get("application_name", ""),
                "subsystem_name": credentials.get("subsystem_name", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="coralogix", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg.model_dump(), "coralogix"
    return None, None
