"""Tests for the centralized session lifecycle owner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness.session import InMemorySessionStorage, Session, SessionManager


@pytest.fixture(autouse=True)
def _no_real_integration_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep bootstrap from resolving real integrations during unit tests.
    monkeypatch.setattr(Session, "warm_resolved_integrations", lambda self, **_k: None)
    monkeypatch.setattr(Session, "hydrate_configured_integrations", lambda self: None)


def _manager(*, repo=None) -> SessionManager:
    return SessionManager(
        storage=InMemorySessionStorage(),
        repo=repo or SimpleNamespace(load_session=lambda _sid: None),
    )


def test_create_opens_storage_and_returns_session() -> None:
    storage = InMemorySessionStorage()
    opened: list[str] = []
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.create()

    assert isinstance(session, Session)
    assert opened == [session.session_id]


def test_create_with_explicit_session_id() -> None:
    session = _manager().create(session_id="fixed-id", open_storage=False)
    assert session.session_id == "fixed-id"


def test_resolve_restores_context_and_reopens_storage() -> None:
    storage = InMemorySessionStorage()
    reopened: list[str] = []
    storage.reopen_session = lambda session_id: reopened.append(session_id)  # type: ignore[method-assign]
    repo = SimpleNamespace(
        load_session=lambda session_id: {
            "session_id": session_id,
            "cli_agent_messages": [("user", "hi"), ("assistant", "hello")],
            "accumulated_context": {"service": "checkout"},
            "history": [{"type": "shell", "text": "ls", "ok": True}],
        }
    )
    manager = SessionManager(storage=storage, repo=repo)

    session = manager.resolve("sess-1")

    assert session.session_id == "sess-1"
    assert session.cli_agent_messages == [("user", "hi"), ("assistant", "hello")]
    assert session.accumulated_context == {"service": "checkout"}
    assert session.history == [{"type": "shell", "text": "ls", "ok": True}]
    assert reopened == ["sess-1"]


def test_restore_context_ignores_empty_and_malformed() -> None:
    manager = _manager()
    session = Session(session_id="s")

    assert manager.restore_context(session, None) is session
    assert session.cli_agent_messages == []

    manager.restore_context(
        session,
        {"cli_agent_messages": [("user", "ok"), "bad-entry", ("system", "x"), ("assistant", "")]},
    )
    # Only well-formed user/assistant pairs with content survive.
    assert session.cli_agent_messages == [("user", "ok")]


def test_rotate_flushes_old_and_creates_new() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.rotate(old_session_id="old-1", new_session_id="new-1")

    assert flushed == ["old-1"]
    assert session.session_id == "new-1"


def test_rotate_without_old_id_skips_flush() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    manager.rotate(new_session_id="new-1")

    assert flushed == []


def test_bootstrap_sets_persistent_task_registry() -> None:
    session = Session(session_id="s")
    before = session.task_registry
    _manager().bootstrap(session)
    assert session.task_registry is not before
