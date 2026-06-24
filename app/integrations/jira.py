"""Jira integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import JiraIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[JiraIntegrationConfig | None, str | None]:
    try:
        cfg = JiraIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "email": credentials.get("email", ""),
                "api_token": credentials.get("api_token", ""),
                "project_key": credentials.get("project_key", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="jira", record_id=record_id)
        return None, None
    if cfg.base_url and cfg.email and cfg.api_token:
        return cfg, "jira"
    return None, None
