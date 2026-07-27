from __future__ import annotations

import pytest

from config.principal import Principal
from gateway.storage import SessionBindingStore, connect_gateway_db


@pytest.fixture
def binding_store(tmp_path) -> SessionBindingStore:
    db_path = tmp_path / "state.db"
    conn = connect_gateway_db(db_path)
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
