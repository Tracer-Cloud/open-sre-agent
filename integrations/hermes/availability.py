"""Backend-aware availability check for Hermes tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def hermes_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when Hermes integration is connected or a fixture backend is injected."""
    hermes = sources.get("hermes", {})
    return bool(hermes.get("connection_verified") or hermes.get("_backend"))
