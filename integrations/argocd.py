"""ArgoCD integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import ArgoCDIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[ArgoCDIntegrationConfig | None, str | None]:
    try:
        cfg = ArgoCDIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "bearer_token": credentials.get("bearer_token", "")
                or credentials.get("auth_token", "")
                or credentials.get("token", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "project": credentials.get("project", ""),
                "app_namespace": credentials.get("app_namespace", ""),
                "verify_ssl": credentials.get("verify_ssl", True),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="argocd", record_id=record_id)
        return None, None
    if cfg.base_url and (cfg.bearer_token or (cfg.username and cfg.password)):
        return cfg, "argocd"
    return None, None
