"""Backend-aware availability check for Bitbucket tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def bitbucket_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when Bitbucket credentials are present or a fixture backend is injected.

    Used by Bitbucket tool wrappers whose ``extract_params`` can delegate to a
    mock ``bitbucket_backend`` for synthetic tests.
    """
    bb = sources.get("bitbucket", {})
    if bb.get("_backend"):
        return True
    return bool(
        bb.get("connection_verified")
        and bb.get("workspace")
        and bb.get("username")
        and bb.get("app_password")
    )
