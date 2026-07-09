"""OpsGenie integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import OpsGenieIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[OpsGenieIntegrationConfig | None, str | None]:
    try:
        cfg = OpsGenieIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "region": credentials.get("region", "us"),
                "integration_id": record_id,
            }
        )
    except ValidationError as exc:
        report_classify_failure(exc, logger=logger, integration="opsgenie", record_id=record_id)
        return None, None
    except Exception as exc:
        logger.warning(
            "classify_failed: integration=opsgenie record_id=%s unexpected error",
            record_id,
            exc_info=True,
        )
        report_classify_failure(exc, logger=logger, integration="opsgenie", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "opsgenie"
    return None, None
