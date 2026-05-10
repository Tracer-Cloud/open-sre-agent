"""Shared helpers for Helm investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.config_models import HelmIntegrationConfig
from app.services.helm import HelmClient


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
    except Exception:
        return None
    return HelmClient(cfg)


def helm_base_unavailable(error: str) -> dict[str, Any]:
    return {"source": "helm", "available": False, "error": error}
