"""Backend-aware availability check for Tempo tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def tempo_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when a verified Tempo config is present OR a fixture backend is injected.

    Used by the Tempo tool wrapper whose ``extract_params`` can delegate to a
    mock ``tempo_backend`` for synthetic tests.
    """
    tempo = sources.get("tempo", {})
    if tempo.get("_backend"):
        return True
    return bool(tempo.get("connection_verified") and tempo.get("url"))
