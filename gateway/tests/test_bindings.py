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
def test_concurrent_writes_do_not_deadlock(tmp_path) -> None:
    import threading
    from gateway.storage import connect_gateway_db, SessionBindingStore

    conn = connect_gateway_db(tmp_path / "state.db")
    store = SessionBindingStore(conn)

    errors = []

    def write(user_id: str) -> None:
        try:
            store.bind(platform="telegram", chat_id=user_id, session_id="s1")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write, args=(str(i),)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn.close()
    assert not errors, f"Concurrent writes failed: {errors}"