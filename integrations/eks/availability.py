"""Backend-aware availability check for EKS tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def eks_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real EKS credentials are present OR a fixture backend is injected.

    Used by EKS tool wrappers whose ``extract_params`` can delegate to a
    mock ``eks_backend`` for synthetic tests.  Tools without backend
    support continue to use the narrower check in
    ``integrations.eks.tools.eks_list_clusters_tool._eks_available``.

    The ``_backend`` slot is reserved for fixture backends that implement
    the EKS tool API (``list_pods``, ``get_pod_logs``, ...). Other backend
    types that speak different protocols should be placed in their own
    distinct source slots and are invisible to this check — the real EKS
    tools stay deactivated for those modes.
    """
    eks = sources.get("eks", {})
    return bool(eks.get("connection_verified") or eks.get("_backend"))
