"""One answer to "which organization does this process serve".

Two deployments name it differently — the Fargate control plane injects
``ORGANIZATION_ID``, the EC2 Slack service sets ``OPENSRE_ORGANIZATION_ID`` —
and every consumer that picked one name saw an empty string on the other
deployment. On Fargate that refused every Slack turn outright.
"""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.organization import organization_id
from config.constants.tenancy import TENANT_ORGANIZATION_ID_ENV

# Exactly the organization-related env a Fargate task definition injects.
FARGATE_ENV = {TENANT_ORGANIZATION_ID_ENV: "org_fargate"}
# What the EC2 Slack service sets instead.
EC2_ENV = {ORGANIZATION_ID_ENV: "org_ec2"}


@pytest.fixture(autouse=True)
def _clear_org_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from neither name set, whatever the developer's shell holds."""
    for name in (TENANT_ORGANIZATION_ID_ENV, ORGANIZATION_ID_ENV):
        monkeypatch.delenv(name, raising=False)


def _apply(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_resolves_on_a_fargate_silo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control plane injects only the bare name."""
    # Arrange
    _apply(monkeypatch, FARGATE_ENV)

    # Act / Assert
    assert organization_id() == "org_fargate"


def test_resolves_on_the_ec2_slack_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """That deployment sets only the prefixed name."""
    # Arrange
    _apply(monkeypatch, EC2_ENV)

    # Act / Assert
    assert organization_id() == "org_ec2"


def test_unbound_when_neither_is_set() -> None:
    """A laptop stays unbound rather than guessing an owner."""
    # Arrange: the autouse fixture cleared both.

    # Act / Assert
    assert organization_id() == ""


def test_prefixed_name_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic ORGANIZATION_ID must not quietly outrank the product's own."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_product")
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org_product")

    # Act / Assert
    assert organization_id() == "org_product"


def test_conflicting_names_warn_and_do_not_silently_pick(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two names disagreeing is a misconfiguration that must be visible.

    Silence here is how this class of bug returns: one deployment updates a
    name, the other does not, and turns are attributed to the wrong customer.
    """
    # Arrange
    import config.constants.organization as organization

    organization._warn_conflicting_names_once.cache_clear()
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_product")
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org_other")

    # Act
    with caplog.at_level("WARNING"):
        resolved = organization_id()

    # Assert
    assert resolved == "org_product"
    assert "different organizations" in caplog.text


def test_whitespace_is_not_an_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank-but-present value must not bind a process to an empty owner."""
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "   ")

    # Act / Assert
    assert organization_id() == ""


def test_a_fargate_silo_can_serve_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this resolver exists for.

    Reading only the prefixed name meant a Fargate silo had no organization, so
    principal resolution refused every Slack turn while the same control plane
    injected that silo's Slack bot token.
    """
    # Arrange
    from gateway.slack.principal import resolve_slack_principal

    _apply(monkeypatch, FARGATE_ENV)
    monkeypatch.delenv("OPENSRE_SILO_TEAM_IDS", raising=False)

    # Act
    principal = resolve_slack_principal(team_id="T_ACME")

    # Assert
    assert principal.id == "org_fargate"


def test_credits_and_usage_see_the_fargate_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metering and attribution read the same answer as everything else."""
    # Arrange
    from gateway.billing.credits_client import organization_id_for_silo
    from platform.analytics.usage_context import get_organization_id

    _apply(monkeypatch, FARGATE_ENV)

    # Act / Assert
    assert organization_id_for_silo() == "org_fargate"
    assert get_organization_id() == "org_fargate"


def test_hydration_still_keys_off_the_control_plane_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hydration asks a different question and must not use the resolver.

    "Did the control plane provision this silo" is not "which organization do we
    serve". An EC2 deployment answers the second and not the first, so resolving
    across both names would read its org as a half-configured silo and raise at
    startup.
    """
    # Arrange
    from gateway.runtime.credential_hydration import CredentialHydrationConfig

    for name in (
        "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN",
        "OPENSRE_INTEGRATIONS_SECRET_ARN",
        "OPENSRE_CREDENTIALS_API_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    _apply(monkeypatch, EC2_ENV)

    # Act / Assert: disabled, not "incomplete configuration".
    assert CredentialHydrationConfig.from_environment() is None
