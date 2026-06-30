"""Backend-aware availability check for SigNoz tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def signoz_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real SigNoz credentials are present OR a fixture backend is injected.

    Used by SigNoz tool wrappers whose ``extract_params`` can delegate to a
    mock ``signoz_backend`` for synthetic tests.
    """
    signoz = sources.get("signoz", {})
    if signoz.get("_backend"):
        return True
    return bool(signoz.get("connection_verified") and signoz.get("url") and signoz.get("api_key"))
