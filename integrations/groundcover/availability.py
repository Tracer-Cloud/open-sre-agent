"""Backend-aware availability check for groundcover tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def groundcover_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real groundcover credentials are present OR a fixture backend is injected.

    Used by groundcover tool wrappers whose ``extract_params`` can delegate to a
    mock ``groundcover_backend`` for synthetic tests.
    """
    gc = sources.get("groundcover", {})
    if gc.get("_backend"):
        return True
    return bool(gc.get("connection_verified") and gc.get("api_key"))
