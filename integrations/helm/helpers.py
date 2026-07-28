"""Build a ``HelmClient`` from raw tool params, or a standard unavailable
response when Helm can't run for this call.

Helm tools call ``helm_client_for_run`` first; if it returns ``None`` they
return ``helm_base_unavailable(...)`` instead of invoking the client.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from core.tool_framework.utils.tool_availability import tool_unavailable
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
    """Validate raw Helm tool params and build a ``HelmClient`` from them.

    Args:
        helm_path: Path or name of the ``helm`` executable; defaults to
            ``"helm"`` on the ``PATH`` when empty.
        kube_context: ``kubectl``/``helm`` context name to target; empty
            means the client's default context.
        kubeconfig: Path to a kubeconfig file; empty means the default
            kubeconfig resolution (``$KUBECONFIG`` / ``~/.kube/config``).
        default_namespace: Namespace to scope Helm operations to when a
            call doesn't specify one; empty means no default namespace.
        integration_id: Store-registered integration id, used to resolve
            per-integration credentials/config; empty for the ad hoc/no-store
            case.

    Returns:
        A ``HelmClient`` configured from the validated params, or ``None``
        if the params fail ``HelmIntegrationConfig`` validation or building
        the config raises unexpectedly (logged at debug level). Callers must
        check for ``None`` and fall back to ``helm_base_unavailable(...)``
        instead of calling into the client.
    """
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
    """Build the standard Helm "unavailable" tool response.

    Args:
        error: Human-readable reason the Helm tool can't run right now
            (e.g. why ``helm_client_for_run`` returned ``None``).

    Returns:
        ``{"source": "helm", "available": False, "error": error}``, the
        envelope Helm tools return directly as their result instead of
        calling into a ``HelmClient``.
    """
    return tool_unavailable("helm", error)
