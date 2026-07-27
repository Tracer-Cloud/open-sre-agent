"""Where each customer's context lives, and what stays shared inside an org.

The layout exists to give a Slack member the same private conversation history a
laptop user has, while the team keeps one set of integration credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope

ACME = Principal.org("org_acme")
GLOBEX = Principal.org("org_globex")
ALICE = Actor.slack("U_ALICE")
BOB = Actor.slack("U_BOB")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every path assertion off the developer's real home directory."""
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    return tmp_path


def _member(principal: Principal, actor: Actor) -> StorageScope:
    return StorageScope(principal=principal, actor=actor)


def test_laptop_run_keeps_the_plain_home_layout(_home: Path) -> None:
    # Arrange: no scope bound, exactly as a terminal run.

    # Act
    org_root = paths.opensre_home()
    member_root = paths.session_home()

    # Assert: nothing is nested, so an existing install is untouched.
    assert org_root == _home
    assert member_root == _home
    assert paths.integrations_store_path() == _home / "integrations.json"


def test_members_of_one_org_share_integration_credentials(_home: Path) -> None:
    # Arrange: two people in the same workspace.

    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        alice_store = paths.integrations_store_path()
    with bound_storage_scope(_member(ACME, BOB)):
        bob_store = paths.integrations_store_path()

    # Assert: credentials belong to the team, not to whoever is speaking.
    assert alice_store == bob_store
    assert ALICE.id not in str(alice_store)


def test_members_of_one_org_keep_separate_conversation_context(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        alice_sessions = paths.session_home()
        alice_memory = paths.get_memory_dir()
    with bound_storage_scope(_member(ACME, BOB)):
        bob_sessions = paths.session_home()
        bob_memory = paths.get_memory_dir()

    # Assert: one member's history is never reachable from another's root.
    assert alice_sessions != bob_sessions
    assert alice_memory != bob_memory
    assert BOB.id not in str(alice_sessions)
    assert ALICE.id not in str(bob_sessions)


def test_member_context_nests_inside_its_own_org(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        member_root = paths.session_home()
        org_root = paths.opensre_home()

    # Assert: deleting the org directory takes its members with it.
    assert org_root in member_root.parents


def test_two_orgs_never_share_a_context_root(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        acme_root = paths.opensre_home()
    with bound_storage_scope(_member(GLOBEX, ALICE)):
        globex_root = paths.opensre_home()

    # Assert: the same person acting in two orgs reads two different stores.
    assert acme_root != globex_root
    assert globex_root not in acme_root.parents
    assert acme_root not in globex_root.parents


def test_the_same_member_id_in_two_orgs_stays_separate(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        acme_alice = paths.session_home()
    with bound_storage_scope(_member(GLOBEX, ALICE)):
        globex_alice = paths.session_home()

    # Assert
    assert acme_alice != globex_alice


@pytest.mark.parametrize("hostile_id", ["../escape", "a/b", "..", "with space"])
def test_ids_that_would_escape_their_directory_are_rejected(hostile_id: str) -> None:
    # Arrange: an id that would climb out of the context root if interpolated.
    scope = _member(Principal.org(hostile_id), ALICE)

    # Act / Assert
    with bound_storage_scope(scope), pytest.raises(paths.UnsafePathSegmentError):
        paths.opensre_home()


def test_hostile_actor_ids_are_rejected_too() -> None:
    # Arrange
    scope = _member(ACME, Actor(id="../../etc"))

    # Act / Assert
    with bound_storage_scope(scope), pytest.raises(paths.UnsafePathSegmentError):
        paths.session_home()
