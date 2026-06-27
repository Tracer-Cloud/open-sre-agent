"""OpenObserve integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import OpenObserveIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[OpenObserveIntegrationConfig | None, str | None]:
    try:
        cfg = OpenObserveIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "org": credentials.get("org", "default"),
                "api_token": credentials.get("api_token", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "stream": credentials.get("stream", ""),
                "max_results": credentials.get("max_results", 100),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="openobserve", record_id=record_id)
        return None, None
    if cfg.base_url and (cfg.api_token or (cfg.username and cfg.password)):
        return cfg, "openobserve"
    return None, None
