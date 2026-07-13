"""Tests for the centralized session lifecycle owner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness.session import (
    InMemorySessionStorage,
    SessionCore,
    SessionManager,
)
from surfaces.interactive_shell.session import (
    Session,
)


@pytest.fixture(autouse=True)
def _no_real_integration_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep bootstrap from resolving real integrations during unit tests.
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)


def _manager(*, repo=None) -> SessionManager:
    return SessionManager(
        storage=InMemorySessionStorage(),
        repo=repo or SimpleNamespace(load_session=lambda _sid: None),
    )


def test_open_storage_opens_bootstrapped_session() -> None:
    storage = InMemorySessionStorage()
    opened: list[str] = []
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="boot-only")
    manager.bootstrap(session, hydrate_integrations=False, persistent_tasks=False)
    manager.open_storage(session)

    assert session.storage is storage
    assert opened == ["boot-only"]


def test_create_opens_storage_and_returns_session() -> None:
    storage = InMemorySessionStorage()
    opened: list[str] = []
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.create()

    assert isinstance(session, SessionCore)
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


def test_rotate_closes_old_and_creates_new() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.rotate(old_session_id="old-1", new_session_id="new-1")

    assert flushed == ["old-1"]
    assert session.session_id == "new-1"


def test_rotate_without_old_id_skips_close() -> None:
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
    assert session.runtime_metadata.get("opensre_version")


def test_created_session_persists_through_manager_storage() -> None:
    # Regression: the session's own storage backend must be the manager's, so
    # session.record() writes to the same place the manager opens/flushes —
    # not the default JSONL field on Session.
    storage = InMemorySessionStorage()
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.create()
    assert session.storage is storage

    session.record("chat", "hello")
    assert any("hello" in str(rec) for rec in storage.read(session.session_id))


def test_close_persists_and_releases_resources() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="s-close")
    session.storage = storage
    session.terminal.background_notices.append("pending notice")
    session.terminal.prompt_refresh_fn = lambda: None

    manager.close(session)

    assert flushed == ["s-close"]
    assert session.terminal.background_notices == []
    assert session.terminal.prompt_refresh_fn is None


def test_close_flush_failure_does_not_crash_teardown() -> None:
    storage = InMemorySessionStorage()

    def _boom(_session: object) -> None:
        raise OSError("disk full")

    storage.flush = _boom  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))
    session = Session(session_id="s-fail")
    session.storage = storage
    session.terminal.prompt_refresh_fn = lambda: None

    # Must not raise; resources still released.
    manager.close(session)
    assert session.terminal.prompt_refresh_fn is None


def test_rotate_in_place_flushes_clears_and_opens_new_id() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    opened: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="old-id")
    session.storage = storage
    session.agent.messages = [("user", "carry")]
    session.accumulated_context["svc"] = "checkout"

    refresh = lambda: None  # noqa: E731 — loop-owned prompt hook stand-in
    session.terminal.prompt_refresh_fn = refresh

    manager.rotate_in_place(session)

    assert flushed == ["old-id"]
    assert session.session_id != "old-id"
    assert opened == [session.session_id]
    assert session.agent.messages == []
    assert session.accumulated_context == {}
    # Regression: in-place reuse must NOT drop the loop-owned prompt hook.
    assert session.terminal.prompt_refresh_fn is refresh


def test_rebind_for_resume_switches_id_and_reopens_storage() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    reopened: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    storage.reopen_session = lambda session_id: reopened.append(session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="live-id")
    session.storage = storage
    refresh = lambda: None  # noqa: E731 — loop-owned prompt hook stand-in
    session.terminal.prompt_refresh_fn = refresh

    manager.rebind_for_resume(session, session_id="saved-id", started_at="2026-01-15T10:00:00")

    assert flushed == ["live-id"]
    assert session.session_id == "saved-id"
    assert reopened == ["saved-id"]
    # Regression: /resume reuses the live handle — keep the prompt hook.
    assert session.terminal.prompt_refresh_fn is refresh


def test_rebind_for_resume_same_id_clears_without_flush() -> None:
    storage = InMemorySessionStorage()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(storage=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="same-id")
    session.storage = storage
    session.history = [{"type": "shell", "text": "ls", "ok": True}]

    manager.rebind_for_resume(session, session_id="same-id")

    assert flushed == []
    assert session.session_id == "same-id"
    assert session.history == []


def test_closed_session_is_garbage_collectable() -> None:
    # Memory-leak guard: after close(), dropping the last strong reference must
    # let the session be collected — no lingering references (warm task,
    # background closures, prompt hook) keep it alive.
    import gc
    import weakref

    manager = _manager()
    session = Session(session_id="s-gc")
    session.storage = InMemorySessionStorage()
    session.terminal.prompt_refresh_fn = lambda: None
    session.terminal.background_notices.append("x")
    ref = weakref.ref(session)

    manager.close(session)
    del session
    gc.collect()

    assert ref() is None, "closed session was not garbage-collected — reference leak"


def test_close_cancels_in_flight_warm_task() -> None:
    manager = _manager()
    session = manager.create(session_id="s-warm")

    class _FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    task = _FakeTask()
    session.integrations._warm_task = task

    manager.close(session)

    assert task.cancelled is True
    assert session.integrations._warm_task is None
