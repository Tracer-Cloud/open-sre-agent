from __future__ import annotations

import pytest

from gateway.storage import SessionBindingStore, connect_gateway_db


@pytest.fixture
def binding_store(tmp_path) -> SessionBindingStore:
    db_path = tmp_path / "state.db"
    conn = connect_gateway_db(db_path)
    store = SessionBindingStore(conn)
    yield store
    conn.close()


def test_bind_and_get(binding_store: SessionBindingStore) -> None:
    binding_store.bind(platform="telegram", chat_id="123", session_id="uuid-1")
    assert binding_store.get_session_id(platform="telegram", chat_id="123") == "uuid-1"


def test_rotate_assigns_new_session(binding_store: SessionBindingStore) -> None:
    binding_store.bind(platform="telegram", chat_id="123", session_id="uuid-1")
    new_id = binding_store.rotate(platform="telegram", chat_id="123")
    assert new_id != "uuid-1"
    assert binding_store.get_session_id(platform="telegram", chat_id="123") == new_id
