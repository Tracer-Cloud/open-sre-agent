"""AWS integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import AWSIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AWSIntegrationConfig | None, str | None]:
    raw: dict[str, Any] = {
        "region": credentials.get("region", "us-east-1"),
        "role_arn": credentials.get("role_arn", ""),
        "external_id": credentials.get("external_id", ""),
        "integration_id": record_id,
    }
    if credentials.get("access_key_id") and credentials.get("secret_access_key"):
        raw["credentials"] = {
            "access_key_id": credentials.get("access_key_id", ""),
            "secret_access_key": credentials.get("secret_access_key", ""),
            "session_token": credentials.get("session_token", ""),
        }
    try:
        return AWSIntegrationConfig.model_validate(raw), "aws"
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="aws", record_id=record_id)
        return None, None
