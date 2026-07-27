"""Which principal owns a Slack turn, and what happens when that is unknown."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.slack import SLACK_SILO_TEAM_IDS_ENV
from config.principal import Principal
from gateway.slack import installs as slack_installs
from gateway.slack import principal as principal_resolve
from gateway.slack.installs import (
    SlackInstallLookupError,
    get_slack_install,
    upsert_slack_install,
)
from gateway.slack.principal import (
    PrincipalResolutionError,
    resolve_slack_principal,
    resolve_slack_scope,
)
from gateway.storage.db import connect_gateway_db

ACME_TEAM = "T_ACME"
ACME_ORG = "org_acme"
GLOBEX_TEAM = "T_GLOBEX"
GLOBEX_ORG = "org_globex"


@pytest.fixture
def install_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Point the install catalog at a throwaway database."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(
        "gateway.storage.db.default_gateway_db_path", lambda: db_path, raising=False
    )
    conn = connect_gateway_db(db_path)
    # Lookups own and close their connection, so hand each one a fresh handle.
    monkeypatch.setattr(
        slack_installs, "connect_gateway_db", lambda *_a, **_k: connect_gateway_db(db_path)
    )
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _no_silo_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test states its own fallback rather than inheriting the developer's env."""
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    monkeypatch.delenv(SLACK_SILO_TEAM_IDS_ENV, raising=False)


def test_installed_team_resolves_to_its_own_org(install_db: sqlite3.Connection) -> None:
    # Arrange: two workspaces, each installed to a different organization.
    upsert_slack_install(team_id=ACME_TEAM, clerk_org_id=ACME_ORG, conn=install_db)
    upsert_slack_install(team_id=GLOBEX_TEAM, clerk_org_id=GLOBEX_ORG, conn=install_db)

    # Act
    acme = resolve_slack_principal(team_id=ACME_TEAM)
    globex = resolve_slack_principal(team_id=GLOBEX_TEAM)

    # Assert: neither workspace can be attributed to the other's org.
    assert acme == Principal.org(ACME_ORG)
    assert globex == Principal.org(GLOBEX_ORG)
    assert acme != globex


def test_install_wins_over_the_silo_organization(
    install_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a silo org is configured, and the team also has its own install.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_silo_should_not_win")
    upsert_slack_install(team_id=ACME_TEAM, clerk_org_id=ACME_ORG, conn=install_db)

    # Act
    resolved = resolve_slack_principal(team_id=ACME_TEAM)

    # Assert: install context is more specific, so it decides.
    assert resolved == Principal.org(ACME_ORG)


@pytest.mark.usefixtures("install_db")
def test_uninstalled_team_falls_back_to_the_silo_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_silo")

    # Act
    resolved = resolve_slack_principal(team_id="T_UNKNOWN")

    # Assert
    assert resolved == Principal.org("org_silo")


@pytest.mark.usefixtures("install_db")
def test_unlisted_team_refused_when_silo_allowlist_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an allowlist configured, an uninstalled team outside it must not be
    attributed to the silo org (prod fail-closed posture)."""
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_silo")
    monkeypatch.setenv(SLACK_SILO_TEAM_IDS_ENV, "T_OWNER, T_OTHER_OWNER")

    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id="T_STRANGER")


@pytest.mark.usefixtures("install_db")
def test_allowlisted_team_uses_the_silo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team named in the allowlist still resolves to the silo org."""
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_silo")
    monkeypatch.setenv(SLACK_SILO_TEAM_IDS_ENV, "T_OWNER, T_UNKNOWN")

    assert resolve_slack_principal(team_id="T_UNKNOWN") == Principal.org("org_silo")


@pytest.mark.usefixtures("install_db")
def test_unknown_team_with_no_silo_org_refuses_to_resolve() -> None:
    # Arrange: nothing identifies an owner for this workspace.

    # Act / Assert: it raises rather than inventing one.
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id="T_UNKNOWN")


def test_unreadable_catalog_refuses_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog outage must not be read as "this team has no install"."""

    # Arrange: the silo org would be a tempting fallback if the error were swallowed.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_silo_must_not_be_used")

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("catalog unavailable")

    monkeypatch.setattr(slack_installs, "connect_gateway_db", _explode)

    # Act / Assert
    with pytest.raises(SlackInstallLookupError):
        get_slack_install(ACME_TEAM)
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id=ACME_TEAM)


def test_turn_without_a_team_id_refuses_to_resolve() -> None:
    with pytest.raises(PrincipalResolutionError):
        resolve_slack_principal(team_id="   ")


def test_members_of_one_team_share_the_org_but_stay_distinct_actors(
    install_db: sqlite3.Connection,
) -> None:
    # Arrange: two people in the same workspace.
    upsert_slack_install(team_id=ACME_TEAM, clerk_org_id=ACME_ORG, conn=install_db)

    # Act
    alice = resolve_slack_scope(team_id=ACME_TEAM, user_id="U_ALICE")
    bob = resolve_slack_scope(team_id=ACME_TEAM, user_id="U_BOB")

    # Assert: one owner for credentials and billing, two identities for the humans.
    assert alice.principal == bob.principal == Principal.org(ACME_ORG)
    assert alice.actor.id != bob.actor.id


def test_install_upsert_replaces_the_org_for_a_team(install_db: sqlite3.Connection) -> None:
    # Arrange: a workspace initially installed to one org.
    upsert_slack_install(team_id=ACME_TEAM, clerk_org_id=ACME_ORG, conn=install_db)

    # Act: the same workspace is reinstalled under a different org.
    upsert_slack_install(team_id=ACME_TEAM, clerk_org_id=GLOBEX_ORG, conn=install_db)

    # Assert: no stale row keeps pointing at the previous owner.
    assert resolve_slack_principal(team_id=ACME_TEAM) == Principal.org(GLOBEX_ORG)
    assert principal_resolve.resolve_slack_principal(team_id=ACME_TEAM).id == GLOBEX_ORG
