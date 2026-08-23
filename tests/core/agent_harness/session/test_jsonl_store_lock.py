"""Cross-process write lock on the session JSONL store (horizontal scale-out).

The lock serializes writes to one session file across tasks. It is opt-in via
``OPENSRE_SESSION_FILE_LOCK`` so a single-task deployment pays nothing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants.session_store import OPENSRE_SESSION_FILE_LOCK_ENV
from core.agent_harness.session.persistence import jsonl_store
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.persistence.paths import session_path


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session storage at a temp home so nothing touches ~/.opensre."""
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def _session(session_id: str) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        started_at=0.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def test_writes_take_no_lock_by_default(storage_home: Path) -> None:
    # Arrange: default (flag unset) — single-task behavior, no lock file.
    session = _session("sess-nolock")
    store = JsonlSessionStore()

    # Act.
    store.open_session(session)
    store.append_turn(session, "chat", "one")

    # Assert: the turn is written and no .lock file was ever created.
    path = session_path(session.session_id)
    assert "one" in path.read_text(encoding="utf-8")
    assert not Path(f"{path}.lock").exists()


def test_enabled_lock_skips_a_write_another_holder_is_blocking(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: enable the lock with a short timeout, and open a session.
    from filelock import FileLock

    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setattr(jsonl_store, "_SESSION_LOCK_TIMEOUT_SECONDS", 0.2)
    session = _session("sess-lock")
    store = JsonlSessionStore()
    store.open_session(session)
    path = session_path(session.session_id)
    before = path.read_text(encoding="utf-8")

    # Act: another task holds the write lock while this store tries to append.
    held = FileLock(f"{path}.lock", timeout=0.2)
    held.acquire()
    try:
        store.append_turn(session, "chat", "blocked")
    finally:
        held.release()

    # Assert: the contended write was skipped (best-effort), never interleaved.
    assert path.read_text(encoding="utf-8") == before


def test_flush_completes_with_the_lock_enabled(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lock is reentrant: flush holds it across its inner appends (leaf, goal,
    # messages) without deadlocking on its own OS lock.
    import json

    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setattr(jsonl_store, "_SESSION_LOCK_TIMEOUT_SECONDS", 1.0)
    session = _session("sess-flush-locked")
    store = JsonlSessionStore()
    store.open_session(session)
    store.append_turn(session, "chat", "one")

    store.flush(session)

    lines = session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line).get("type") == "leaf" for line in lines)
