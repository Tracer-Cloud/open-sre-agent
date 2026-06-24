"""VictoriaLogs integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import VictoriaLogsIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = VictoriaLogsIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "tenant_id": credentials.get("tenant_id"),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(
            exc, logger=logger, integration="victoria_logs", record_id=record_id
        )
        return None, None
    if cfg.base_url:
        return cfg.model_dump(), "victoria_logs"
    return None, None
