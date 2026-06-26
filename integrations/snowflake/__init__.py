"""Snowflake integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import SnowflakeIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = SnowflakeIntegrationConfig.model_validate(
            {
                "account_identifier": credentials.get(
                    "account_identifier", credentials.get("account", "")
                ),
                "token": credentials.get("token", ""),
                "user": credentials.get("user", ""),
                "password": credentials.get("password", ""),
                "warehouse": credentials.get("warehouse", ""),
                "role": credentials.get("role", ""),
                "database": credentials.get("database", ""),
                "schema": credentials.get("schema", ""),
                "max_results": credentials.get("max_results", 50),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="snowflake", record_id=record_id)
        return None, None
    if cfg.account_identifier and cfg.token:
        config = cfg.model_dump(exclude_none=True)
        config["schema"] = config.pop("db_schema", "")
        return config, "snowflake"
    return None, None
