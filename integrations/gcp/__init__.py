"""Google Cloud Platform integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import GCPIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GCPIntegrationConfig | None, str | None]:
    """Normalize stored GCP credentials into a config, or return ``(None, None)``.

    Only ``project_id`` is required. Every authentication field is optional
    because Application Default Credentials — what a GKE pod with Workload
    Identity already has — need no configuration.
    """
    try:
        cfg = GCPIntegrationConfig.model_validate(
            {
                "project_id": credentials.get("project_id", ""),
                "additional_projects": credentials.get("additional_projects", []),
                "service_account_key": credentials.get("service_account_key", ""),
                "impersonate_service_account": credentials.get("impersonate_service_account", ""),
                "max_results": credentials.get("max_results", 100),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="gcp", record_id=record_id)
        return None, None
    if cfg.project_id:
        return cfg, "gcp"
    return None, None
