"""Shared helpers for Helm investigation tools."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations.config_models import HelmIntegrationConfig
from integrations.helm.client import HelmClient

logger = logging.getLogger(__name__)


def helm_client_for_run(
    helm_path: str = "helm",
    kube_context: str = "",
    kubeconfig: str = "",
    default_namespace: str = "",
    integration_id: str = "",
) -> HelmClient | None:
    try:
        cfg = HelmIntegrationConfig.model_validate(
            {
                "helm_path": helm_path or "helm",
                "kube_context": kube_context or "",
                "kubeconfig": kubeconfig or "",
                "default_namespace": default_namespace or "",
                "integration_id": integration_id or "",
            }
        )
    except ValidationError:
        return None
    except Exception:
        logger.debug("helm_client_for_run failed unexpectedly", exc_info=True)
        return None
    return HelmClient(cfg)


def helm_base_unavailable(error: str) -> dict[str, Any]:
    return {"source": "helm", "available": False, "error": error}
