"""Which organization owns a Slack turn, and when a turn is refused.

Resolution decides whose credentials are read and who is billed, so every path
that could attribute a turn to the wrong organization is pinned here. There is
no install catalog: the team → organization mapping is control-plane data the
webapp owns, and this deployment is told which organization it serves.
"""

from __future__ import annotations

import logging

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.slack import SLACK_SILO_TEAM_IDS_ENV
from config.principal import Principal
from gateway.transports.slack.principal import (
    PrincipalResolutionError,
    resolve_slack_principal,
    resolve_slack_scope,
    slack_scope,
)

ACME_TEAM = "T_ACME"
ACME_ORG = "org_acme"
OTHER_TEAM = "T_OTHER"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test states its own configuration rather than inheriting the developer's."""
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    monkeypatch.delenv(SLACK_SILO_TEAM_IDS_ENV, raising=False)


def test_configured_organization_owns_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)

    # Act
    resolved = resolve_slack_principal(team_id=ACME_TEAM)

    # Assert
    assert resolved == Principal.org(ACME_ORG)


def test_turn_is_refused_when_no_organization_is_configured() -> None:
    """Without a configured owner there is nothing to attribute the turn to."""
    # Act / Assert
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id=ACME_TEAM)


def test_turn_without_a_team_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a configured org is not enough if the turn names no team.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)

    # Act / Assert
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id="   ")


def test_allowlisted_team_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)
    monkeypatch.setenv(SLACK_SILO_TEAM_IDS_ENV, f"{ACME_TEAM}, T_SECOND")

    # Act
    resolved = resolve_slack_principal(team_id=ACME_TEAM)

    # Assert
    assert resolved == Principal.org(ACME_ORG)


def test_unlisted_team_is_refused_when_an_allowlist_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace that merely installed the app must not inherit real credentials."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)
    monkeypatch.setenv(SLACK_SILO_TEAM_IDS_ENV, ACME_TEAM)

    # Act / Assert
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id=OTHER_TEAM)


def test_permissive_mode_warns_so_the_exposure_is_visible(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without an allowlist any team is served, which must not be silent."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)

    # Act
    with caplog.at_level(logging.WARNING):
        resolved = resolve_slack_principal(team_id=OTHER_TEAM)

    # Assert: served, but the operator is told how to restrict it.
    assert resolved == Principal.org(ACME_ORG)
    assert SLACK_SILO_TEAM_IDS_ENV in caplog.text


def test_allowlisted_mode_does_not_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)
    monkeypatch.setenv(SLACK_SILO_TEAM_IDS_ENV, ACME_TEAM)

    # Act
    with caplog.at_level(logging.WARNING):
        resolve_slack_principal(team_id=ACME_TEAM)

    # Assert: the restricted posture is the expected one, so it stays quiet.
    assert SLACK_SILO_TEAM_IDS_ENV not in caplog.text


def test_members_of_one_team_share_the_org_but_stay_distinct_actors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME_ORG)

    # Act
    alice = resolve_slack_scope(team_id=ACME_TEAM, user_id="U_ALICE")
    bob = resolve_slack_scope(team_id=ACME_TEAM, user_id="U_BOB")

    # Assert: one owner for credentials and billing, two identities for the humans.
    assert alice.principal == bob.principal == Principal.org(ACME_ORG)
    assert alice.actor.id != bob.actor.id


def test_scope_helper_pairs_the_organization_with_the_speaker() -> None:
    # Act
    scope = slack_scope(Principal.org(ACME_ORG), "U_ALICE")

    # Assert
    assert scope.principal == Principal.org(ACME_ORG)
    assert scope.actor.id == "U_ALICE"
