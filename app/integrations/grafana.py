"""Grafana integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import GrafanaIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": credentials.get("endpoint", ""),
                "api_key": credentials.get("api_key", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    if not cfg.endpoint:
        return None, None
    if cfg.is_local:
        return {
            "endpoint": cfg.endpoint,
            "api_key": "",
            "username": cfg.username,
            "password": cfg.password,
            "integration_id": cfg.integration_id,
        }, "grafana_local"
    if cfg.api_key and cfg.api_key != "local":
        return cfg.model_dump(), "grafana"
    return None, None
