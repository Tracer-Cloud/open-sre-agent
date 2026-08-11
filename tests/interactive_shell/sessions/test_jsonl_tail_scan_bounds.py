"""Cold tip scan: I/O must be linear in the trailing bytes, and edge records safe.

Review findings on the tail-window scan:

* Each window expansion re-read every already-seen trailing byte
  (``read(window)`` from ``size - window``), so a long run of trailing
  ``trace_span`` rows made cold resume O(bytes²/chunk) in I/O.
* A trailing ``type: session`` record (concatenated or corrupt file) returned
  ``None`` immediately, forking the chain, instead of being skipped like the
  old full scan did.
* The tip cache was keyed by session id alone, while the file path depends on
  the bound storage scope — a reused id across scopes could parent off the
  wrong file's tip.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants import paths
from core.agent_harness.session.persistence.jsonl_store import (
    _TAIL_SCAN_CHUNK_BYTES,
    JsonlSessionStore,
)
from core.agent_harness.session.persistence.paths import session_path


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    return tmp_path


def _session(session_id: str = "sess-1") -> Any:
    return SimpleNamespace(
        session_id=session_id,
        started_at=1_700_000_000.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def _write_line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def test_cold_scan_io_is_linear_over_trailing_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-window run of trailing spans must not re-read earlier bytes.

    Total bytes read may exceed the file once (window alignment), but not by
    the quadratic factor the expanding re-read produced.
    """
    # Arrange: one real message, then ~4 windows of trailing trace spans.
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="tip")
    path = session_path(session.session_id)
    filler = "x" * 512
    span_line = json.dumps({"id": "s", "parent_id": None, "type": "trace_span", "name": filler})
    target = 4 * _TAIL_SCAN_CHUNK_BYTES
    with path.open("a", encoding="utf-8") as fh:
        written = 0
        while written < target:
            fh.write(span_line + "\n")
            written += len(span_line) + 1
    size = path.stat().st_size

    read_bytes = {"total": 0}
    real_open = Path.open

    class _CountingReader:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def read(self, n: int = -1) -> bytes:
            data = self._inner.read(n)
            read_bytes["total"] += len(data)
            return data

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def __enter__(self) -> _CountingReader:
            self._inner.__enter__()
            return self

        def __exit__(self, *exc: Any) -> Any:
            return self._inner.__exit__(*exc)

    def counting_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        if "b" in mode and self == path:
            return _CountingReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", counting_open)

    # Act: cold instance resolves the tip.
    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="user", content="after")

    # Assert: linear I/O — at most ~2× the file, not the quadratic ~2.5×+
    # (expanding re-read gives chunk+2c+3c+4c+5c = 15c ≈ 3.7× here).
    assert read_bytes["total"] <= 2 * size, (
        f"cold scan read {read_bytes['total']} bytes of a {size}-byte file — "
        "window expansion is re-reading already-seen bytes"
    )
    # And correctness held: the append chained to the real tip.
    lines = path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    tip = next(json.loads(ln) for ln in lines if '"content": "tip"' in ln)
    assert last["parent_id"] == tip["id"]


def test_trailing_session_record_is_skipped_not_a_chain_fork() -> None:
    """A stray header mid-file must not reset the parent chain."""
    # Arrange: normal session, then a concatenated stray header line.
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="real-tip")
    path = session_path(session.session_id)
    _write_line(path, {"type": "session", "version": 2, "id": "stray"})

    # Act: cold instance appends.
    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="assistant", content="next")

    # Assert: chained to the real tip, not orphaned at the fork.
    lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    real_tip = next(r for r in lines if r.get("content") == "real-tip")
    appended = next(r for r in lines if r.get("content") == "next")
    assert appended["parent_id"] == real_tip["id"]


def test_tip_cache_is_scoped_to_the_file_not_just_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same session id under two homes must not share a cached tip."""
    # Arrange: one storage instance, same id, two storage homes.
    storage = JsonlSessionStore()
    session = _session("shared-id")

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "home-a")
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="a-tip")
    path_a = session_path(session.session_id)

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "home-b")
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="b-tip")

    # Act: append in home B, then back in home A.
    storage.append_message(session.session_id, role="assistant", content="b-next")
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "home-a")
    storage.append_message(session.session_id, role="assistant", content="a-next")

    # Assert: each home's chain is internally consistent.
    a_records = [json.loads(ln) for ln in path_a.read_text(encoding="utf-8").splitlines()]
    a_tip = next(r for r in a_records if r.get("content") == "a-tip")
    a_next = next(r for r in a_records if r.get("content") == "a-next")
    assert a_next["parent_id"] == a_tip["id"], (
        "append under home A parented off home B's tip — cache ignores the path"
    )
