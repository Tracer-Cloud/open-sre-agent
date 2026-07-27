from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from gateway.storage import SessionBindingStore
from gateway.storage.db import bindings_db_path, connect_bindings_db, default_gateway_db_path


@pytest.fixture
def binding_store(tmp_path) -> SessionBindingStore:
    db_path = tmp_path / "state.db"
    conn = connect_bindings_db(db_path)
    store = SessionBindingStore(conn)
    yield store
    conn.close()


@pytest.fixture
def principal() -> Principal:
    return Principal.individual("test-user-1")


def test_bind_and_get(binding_store: SessionBindingStore, principal: Principal) -> None:
    binding_store.bind(
        platform="telegram",
        chat_id="123",
        session_id="uuid-1",
        principal=principal,
    )
    assert (
        binding_store.get_session_id(
            platform="telegram",
            chat_id="123",
            principal=principal,
        )
        == "uuid-1"
    )


def test_rotate_assigns_new_session(
    binding_store: SessionBindingStore, principal: Principal
) -> None:
    binding_store.bind(
        platform="telegram",
        chat_id="123",
        session_id="uuid-1",
        principal=principal,
    )
    new_id = binding_store.rotate(
        platform="telegram",
        chat_id="123",
        principal=principal,
    )
    assert new_id != "uuid-1"
    assert (
        binding_store.get_session_id(
            platform="telegram",
            chat_id="123",
            principal=principal,
        )
        == new_id
    )


def test_bindings_are_isolated_by_principal(binding_store: SessionBindingStore) -> None:
    a = Principal.org("org-a")
    b = Principal.org("org-b")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-a",
        principal=a,
    )
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-b",
        principal=b,
    )
    assert binding_store.get_session_id(platform="slack", chat_id="T:C:1", principal=a) == "sess-a"
    assert binding_store.get_session_id(platform="slack", chat_id="T:C:1", principal=b) == "sess-b"


def test_bindings_are_isolated_by_actor(binding_store: SessionBindingStore) -> None:
    org = Principal.org("org-a")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-alice",
        principal=org,
        actor="U_ALICE",
    )
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-bob",
        principal=org,
        actor="U_BOB",
    )
    assert (
        binding_store.get_session_id(
            platform="slack", chat_id="T:C:1", principal=org, actor="U_ALICE"
        )
        == "sess-alice"
    )
    assert (
        binding_store.get_session_id(
            platform="slack", chat_id="T:C:1", principal=org, actor="U_BOB"
        )
        == "sess-bob"
    )


def test_has_any_actor_binding_sees_any_member(binding_store: SessionBindingStore) -> None:
    org = Principal.org("org-a")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:thread1",
        session_id="sess-alice",
        principal=org,
        actor="U_ALICE",
    )
    assert binding_store.has_any_actor_binding(
        platform="slack",
        chat_id="T:C:thread1",
        principal=org,
    )
    assert not binding_store.has_any_actor_binding(
        platform="slack",
        chat_id="T:C:other",
        principal=org,
    )


def test_bindings_live_on_the_org_context_root_not_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced task must still resolve a thread to the transcript it has.

    The install catalog stays on the host because it answers "which org is
    this?" before an org is known; bindings follow the org's mounted volume.
    """
    # Arrange: a mounted context root, separate from the host home.
    host = tmp_path / "host"
    mount = tmp_path / "mount"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", host)
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_acme")

    # Act
    with bound_storage_scope(
        StorageScope(principal=Principal.org("org_acme"), actor=Actor(id="U_ALICE"))
    ):
        bindings = bindings_db_path()
    installs = default_gateway_db_path()

    # Assert: bindings persist on the volume, installs stay on the host.
    assert bindings == mount / "gateway" / "bindings.db"
    assert installs == host / "gateway" / "state.db"


def test_bindings_survive_a_new_store_over_the_same_volume(tmp_path: Path) -> None:
    # Arrange: one task binds a thread to a session.
    db_path = tmp_path / "bindings.db"
    first = SessionBindingStore(connect_bindings_db(db_path))
    first.bind(platform="slack", chat_id="T1:C1:100.1", session_id="s-1", actor="U_ALICE")
    first.close()

    # Act: the task is replaced and a fresh store opens the same volume.
    second = SessionBindingStore(connect_bindings_db(db_path))
    resolved = second.get_session_id(platform="slack", chat_id="T1:C1:100.1", actor="U_ALICE")
    second.close()

    # Assert: the thread still resolves to its existing session.
    assert resolved == "s-1"


def test_legacy_host_bindings_are_adopted_once(tmp_path: Path) -> None:
    """Upgrading must not silently drop conversations bound before the split."""
    # Arrange: a pre-split database holding bindings next to the installs table.
    gateway_dir_path = tmp_path / "gateway"
    gateway_dir_path.mkdir(parents=True)
    legacy = connect_bindings_db(gateway_dir_path / "state.db")
    SessionBindingStore(legacy).bind(platform="telegram", chat_id="42", session_id="legacy-session")
    legacy.close()

    # Act: the new bindings database opens alongside it.
    store = SessionBindingStore(connect_bindings_db(gateway_dir_path / "bindings.db"))
    resolved = store.get_session_id(platform="telegram", chat_id="42")
    store.close()

    # Assert: the existing conversation is carried over, not lost.
    assert resolved == "legacy-session"
