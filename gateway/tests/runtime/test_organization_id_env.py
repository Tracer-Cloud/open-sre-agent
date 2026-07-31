"""Hydration gates on the bootstrap secret, not on an organization name.

The control plane's ECS task definition supplies ``ORGANIZATION_ID``; the EC2
Slack service sets ``OPENSRE_ORGANIZATION_ID``. The two names are converging,
so hydration resolves either through
:func:`config.constants.organization.organization_id` — but only once
``OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN`` says the control plane provisioned
this task. Every deployment serves an organization, so gating on the name
would make an EC2 deployment look like a half-configured silo and fail
startup. These tests pin that boundary.
"""

from __future__ import annotations

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


def test_either_organization_name_satisfies_a_provisioned_silo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silo with a bootstrap secret hydrates under either organization name.

    The two names are converging, so a task definition mid-rollout may carry
    only one of them.
    """
    # Arrange: the fixture already set the bootstrap ARN.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-from-either-name")

    # Act
    config = CredentialHydrationConfig.from_environment()

    # Assert
    assert config is not None
    assert config.organization_id == "org-from-either-name"


def test_an_organization_without_a_bootstrap_secret_is_not_a_silo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serving an organization does not by itself mean the control plane ran.

    Every deployment names an organization. Only a provisioned silo has a
    bootstrap secret, so keying off the name would make the EC2 service look
    half-configured and raise at gateway startup.
    """
    # Arrange
    monkeypatch.delenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-ec2")

    # Act / Assert: disabled, not "incomplete".
    assert CredentialHydrationConfig.from_environment() is None


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
