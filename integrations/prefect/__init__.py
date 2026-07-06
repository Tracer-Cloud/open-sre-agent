"""Prefect integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import PrefectIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[PrefectIntegrationConfig | None, str | None]:
    # Self-hosted Prefect Server needs only ``api_url`` (no key); Prefect Cloud
    # needs an ``api_key``. Require one of the two explicitly — the config
    # model's own ``api_url`` default (Prefect Cloud's base URL) must not, by
    # itself, count as "configured".
    raw_api_url = str(credentials.get("api_url", "")).strip()
    raw_api_key = str(credentials.get("api_key", "")).strip()
    if not raw_api_url and not raw_api_key:
        return None, None
    try:
        cfg = PrefectIntegrationConfig.model_validate(
            {
                "api_url": raw_api_url,
                "api_key": raw_api_key,
                "account_id": credentials.get("account_id", ""),
                "workspace_id": credentials.get("workspace_id", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="prefect", record_id=record_id)
        return None, None
    return cfg, "prefect"
