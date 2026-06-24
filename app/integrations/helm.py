"""Helm integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations._validation_helpers import report_classify_failure
from app.integrations.config_models import HelmIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = HelmIntegrationConfig.model_validate(
            {
                "helm_path": credentials.get("helm_path", "helm"),
                "kube_context": credentials.get("kube_context", "")
                or credentials.get("context", ""),
                "kubeconfig": credentials.get("kubeconfig", "")
                or credentials.get("kubeconfig_path", "")
                or credentials.get("kube_config", ""),
                "default_namespace": credentials.get("default_namespace", "")
                or credentials.get("namespace", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="helm", record_id=record_id)
        return None, None
    return cfg.model_dump(), "helm"
