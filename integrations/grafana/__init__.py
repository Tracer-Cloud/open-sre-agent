"""Grafana integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import GrafanaIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GrafanaIntegrationConfig | None, str | None]:
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
    except ValidationError as exc:
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    except Exception as exc:
        logger.warning("unexpected error classifying grafana config", exc_info=True)
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    if not cfg.endpoint:
        return None, None
    if cfg.is_local:
        # Clear api_key for local grafana — basic auth (username/password) is used instead.
        return cfg.model_copy(update={"api_key": ""}), "grafana_local"
    if cfg.api_key and cfg.api_key != "local":
        return cfg, "grafana"
    return None, None
