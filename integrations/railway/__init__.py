from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import RailwayIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[RailwayIntegrationConfig | None, str | None]:
    try:
        config = RailwayIntegrationConfig.model_validate(
            {
                "token": credentials.get("token", "") or credentials.get("api_token", ""),
                "railway_path": credentials.get("railway_path", "railway"),
                "project": credentials.get("project", "") or credentials.get("project_id", ""),
                "service": credentials.get("service", "") or credentials.get("service_id", ""),
                "environment": credentials.get("environment", "")
                or credentials.get("environment_id", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="railway", record_id=record_id)
        return None, None
    if config.token or config.has_default_scope:
        return config, "railway"
    return None, None
