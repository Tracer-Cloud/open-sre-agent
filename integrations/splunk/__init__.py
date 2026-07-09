"""Splunk integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import SplunkIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[SplunkIntegrationConfig | None, str | None]:
    try:
        cfg = SplunkIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "token": credentials.get("token", ""),
                "index": credentials.get("index", "main"),
                "verify_ssl": credentials.get("verify_ssl", True),
                "ca_bundle": credentials.get("ca_bundle", ""),
                "integration_id": record_id,
            }
        )
    except ValidationError as exc:
        report_classify_failure(exc, logger=logger, integration="splunk", record_id=record_id)
        return None, None
    except Exception as exc:
        logger.warning(
            "classify_failed: integration=splunk record_id=%s unexpected error",
            record_id,
            exc_info=True,
        )
        report_classify_failure(exc, logger=logger, integration="splunk", record_id=record_id)
        return None, None
    if cfg.base_url and cfg.token:
        return cfg, "splunk"
    return None, None
