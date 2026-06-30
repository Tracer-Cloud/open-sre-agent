"""Backend-aware availability check for Datadog tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def datadog_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real Datadog credentials are present OR a fixture backend is injected.

    Used by Datadog tool wrappers whose ``extract_params`` can delegate
    to a mock ``datadog_backend`` for synthetic tests.
    """
    dd = sources.get("datadog", {})
    return bool(dd.get("connection_verified") or dd.get("_backend"))
