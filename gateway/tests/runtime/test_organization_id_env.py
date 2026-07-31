"""Gateway reads the organization id the infra actually injects.

``config.constants.billing`` defines ``ORGANIZATION_ID_ENV`` as
``OPENSRE_ORGANIZATION_ID`` and documents it as "injected by the org-silo infra
(ECS task definition)". Two gateway call sites read a bare ``ORGANIZATION_ID``
instead, so on a real deployment they find nothing and degrade silently —
credential hydration stays off and the Neon run worker reports
``api_runs: not configured`` with no error.

Its sibling variables in the same bootstrap family
(``OPENSRE_CREDENTIALS_API_URL``, ``OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN``)
are both prefixed, which is what marks the bare name as the odd one out rather
than a deliberate control-plane convention.
"""

from __future__ import annotations

from typing import Any

from config.constants.billing import ORGANIZATION_ID_ENV


def test_credential_hydration_reads_the_injected_variable(monkeypatch: Any) -> None:
    # Arrange
    from gateway.runtime.credential_hydration import CredentialHydrationConfig

    monkeypatch.delenv("ORGANIZATION_ID", raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_from_infra")
    monkeypatch.setenv("OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN", "arn:aws:secret")
    monkeypatch.setenv("OPENSRE_CREDENTIALS_API_URL", "https://credentials.example")

    # Act
    config = CredentialHydrationConfig.from_environment()

    # Assert
    assert config is not None, "hydration disabled despite the infra-injected org id"
    assert config.organization_id == "org_from_infra"


def test_remote_runs_read_the_injected_variable(monkeypatch: Any) -> None:
    """The Neon run worker must not report 'not configured' on a real deployment."""
    # Arrange
    import gateway.runtime.manager as manager_module

    monkeypatch.delenv("ORGANIZATION_ID", raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_from_infra")
    source = __import__("inspect").getsource(manager_module.GatewayManager._start_remote_runs)

    # Assert: read through the shared constant, not a bare literal.
    assert '"ORGANIZATION_ID"' not in source, (
        "reads a bare ORGANIZATION_ID the org-silo infra never sets"
    )
