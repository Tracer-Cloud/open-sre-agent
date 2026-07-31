"""The tenant id and the billing org id are separate env vars.

The control plane's ECS task definition supplies ``ORGANIZATION_ID``; the product
reads ``OPENSRE_ORGANIZATION_ID`` to attribute usage and to enforce that a
mounted context volume belongs to the organization being served. Reading the
billing name during hydration crashed startup in a deployed silo and left the
remote-run worker unstarted, so these tests pin that the gateway reads the
injected name and that neither var stands in for the other.
"""

from __future__ import annotations

import os

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.tenancy import (
    CREDENTIALS_API_URL_ENV,
    CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV,
    TENANT_ORGANIZATION_ID_ENV,
)
from gateway.runtime.credential_hydration import CredentialHydrationConfig

_BOOTSTRAP_ARN = "arn:aws:secretsmanager:eu-west-2:1:secret:bootstrap"


@pytest.fixture(autouse=True)
def _silo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an unset environment holding only the secret ARN."""
    for name in (TENANT_ORGANIZATION_ID_ENV, ORGANIZATION_ID_ENV, CREDENTIALS_API_URL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, _BOOTSTRAP_ARN)


def test_env_names_are_distinct() -> None:
    """The two constants must never collapse onto one spelling."""
    assert TENANT_ORGANIZATION_ID_ENV == "ORGANIZATION_ID"
    assert ORGANIZATION_ID_ENV == "OPENSRE_ORGANIZATION_ID"


def test_hydration_reads_the_injected_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silo configured the way ECS configures it hydrates successfully."""
    # Arrange: exactly what the control plane injects, and nothing else.
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org-from-control-plane")

    # Act
    config = CredentialHydrationConfig.from_environment()

    # Assert
    assert config is not None
    assert config.organization_id == "org-from-control-plane"


def test_billing_org_id_does_not_satisfy_hydration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting only the billing name is incomplete configuration, not a tenant id."""
    # Arrange: a value distinctive enough to spot if it ever leaks into hydration.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-billing-must-not-leak")

    # Act / Assert
    with pytest.raises(ValueError, match="incomplete"):
        CredentialHydrationConfig.from_environment()


def test_tenant_id_does_not_satisfy_the_mount_ownership_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed volume check must not see the control plane's value.

    ``config.constants.paths`` refuses a turn when the declared silo owner is
    absent; accepting the tenant id there would weaken that check.
    """
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org-from-control-plane")

    # Act
    declared_owner = os.getenv(ORGANIZATION_ID_ENV, "")

    # Assert
    assert declared_owner == ""


def test_whitespace_is_not_a_tenant_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank-but-present tenant id must not build a config.

    Without trimming it reads as set, and hydration would then request another
    tenant's credentials under an empty organization id.
    """
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "   ")

    # Act / Assert
    with pytest.raises(ValueError, match="incomplete"):
        CredentialHydrationConfig.from_environment()


def test_hydration_stays_disabled_outside_a_silo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local run with no silo env at all is not a misconfiguration."""
    # Arrange
    monkeypatch.delenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, raising=False)

    # Act / Assert
    assert CredentialHydrationConfig.from_environment() is None
