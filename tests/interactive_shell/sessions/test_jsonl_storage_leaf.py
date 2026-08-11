"""JSONL session storage: parent-chain (leaf) semantics and append cost.

The characterization tests pin the observable tree shape — each appended
record's ``parent_id`` — so the leaf-resolution implementation can change
underneath them. The read-count test pins the cost: appending must not re-read
the whole file per entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants import paths
from core.agent_harness.session.persistence import jsonl_store
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
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


def _records(session_id: str) -> list[dict[str, Any]]:
    lines = session_path(session_id).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


# ── Characterization: the tree shape must survive any refactor ──────────────


def test_appends_chain_parent_to_previous_record() -> None:
    # Arrange
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)

    # Act
    storage.append_message(session.session_id, role="user", content="hello")
    storage.append_message(session.session_id, role="assistant", content="hi")
    storage.append_turn(session, "chat", "hello")

    # Assert: header has no parent; each entry points at the one before it.
    records = _records(session.session_id)
    assert [r["type"] for r in records] == ["session", "message", "message", "custom_message"]
    assert records[1]["parent_id"] is None
    assert records[2]["parent_id"] == records[1]["id"]
    assert records[3]["parent_id"] == records[2]["id"]


def test_trace_span_does_not_become_the_leaf() -> None:
    # Arrange
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")

    # Act: a trace span lands, then a normal message.
    storage.append_trace_span(
        session.session_id,
        span_kind="llm",
        name="invoke",
        duration_ms=1000,
    )
    storage.append_message(session.session_id, role="assistant", content="a")

    # Assert: the answer's parent is the question, not the span.
    records = _records(session.session_id)
    question = next(r for r in records if r.get("role") == "user")
    answer = next(r for r in records if r.get("role") == "assistant")
    assert answer["parent_id"] == question["id"]


def test_append_after_leaf_marker_continues_from_the_marked_leaf() -> None:
    # Arrange: a flushed session ends in a "leaf" end-marker.
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_turn(session, "chat", "hello")
    storage.append_message(session.session_id, role="user", content="q")
    storage.flush(session)
    records = _records(session.session_id)
    assert records[-1]["type"] == "leaf"
    marked_leaf_parent = records[-1]["parent_id"]

    # Act: a resumed process appends again.
    resumed = JsonlSessionStore()
    resumed.append_message(session.session_id, role="user", content="again")

    # Assert: the new entry continues from the leaf the marker pointed at.
    new_last = _records(session.session_id)[-1]
    assert new_last["parent_id"] == marked_leaf_parent


def test_fresh_instance_resumes_from_existing_file() -> None:
    # Arrange: one instance writes, another (new process) appends later.
    first = JsonlSessionStore()
    session = _session()
    first.open_session(session)
    first.append_message(session.session_id, role="user", content="q")

    # Act
    second = JsonlSessionStore()
    second.append_message(session.session_id, role="assistant", content="a")

    # Assert
    records = _records(session.session_id)
    assert records[-1]["parent_id"] == records[-2]["id"]


def test_reopen_session_drops_tip_cache_and_rescan() -> None:
    """reopen_session must forget the in-memory tip so disk is the source of truth."""
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")
    assert any(key[0] == session.session_id for key in storage._leaf_ids)

    # Another writer advances the tip on disk while this instance still caches "q".
    other = JsonlSessionStore()
    other.append_message(session.session_id, role="assistant", content="a")

    storage.reopen_session(session.session_id)
    assert not any(key[0] == session.session_id for key in storage._leaf_ids)
    assert session.session_id not in storage._leaf_file_sig

    storage.append_message(session.session_id, role="user", content="follow-up")
    records = _records(session.session_id)
    follow = next(r for r in records if r.get("content") == "follow-up")
    answer = next(r for r in records if r.get("content") == "a")
    assert follow["parent_id"] == answer["id"]


def test_tip_cache_invalidates_when_file_changes_out_of_band() -> None:
    """Warm tip must not parent off a stale id after another writer appends."""
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")

    other = JsonlSessionStore()
    other.append_message(session.session_id, role="assistant", content="a")

    # No reopen_session — file (mtime, size) must invalidate the warm tip.
    storage.append_message(session.session_id, role="user", content="follow-up")
    records = _records(session.session_id)
    follow = next(r for r in records if r.get("content") == "follow-up")
    answer = next(r for r in records if r.get("content") == "a")
    assert follow["parent_id"] == answer["id"]


def test_explicit_parent_still_advances_the_leaf() -> None:
    """A child written with an explicit parent is still the newest record."""
    # Arrange / Act: a tool call writes a call + result pair (the result
    # carries an explicit parent), then a message follows.
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_tool_call(
        session.session_id, tool="grep", arguments={"q": "x"}, result="hit", ok=True
    )
    storage.append_message(session.session_id, role="assistant", content="done")

    # Assert: result chains to call; the message chains to the result.
    records = _records(session.session_id)
    call = next(r for r in records if r["type"] == "tool_call")
    result = next(r for r in records if r["type"] == "tool_result")
    message = next(r for r in records if r["type"] == "message")
    assert result["parent_id"] == call["id"]
    assert message["parent_id"] == result["id"]


# ── Cost: appends must not re-read the file each time ───────────────────────


def test_a_burst_of_appends_reads_the_file_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit finding: every append re-read and re-parsed the whole file.

    Appending N entries from one storage instance may cold-scan once; after
    that the leaf is known and further appends must not read at all.
    """
    # Arrange
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)

    reads = {"count": 0}
    real_read_text = Path.read_text

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".jsonl"):
            reads["count"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    # Act
    for i in range(50):
        storage.append_message(session.session_id, role="user", content=f"m{i}")

    # Assert
    assert reads["count"] <= 1, (
        f"50 appends read the session file {reads['count']} times — "
        "leaf resolution is re-reading the file per append"
    )
    # And the chain is still intact end to end.
    records = _records(session.session_id)
    for prev, cur in zip(records[1:], records[2:], strict=False):
        assert cur["parent_id"] == prev["id"]


def test_cold_leaf_scan_stops_at_the_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resuming a large session must not JSON-parse every historical line."""
    # Arrange: a file with many records, then a fresh instance (cold cache).
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    for i in range(200):
        storage.append_message(session.session_id, role="user", content=f"m{i}")

    parsed = {"count": 0}
    real_loads = json.loads

    def counting_loads(s: Any, *args: Any, **kwargs: Any) -> Any:
        parsed["count"] += 1
        return real_loads(s, *args, **kwargs)

    monkeypatch.setattr(jsonl_store.json, "loads", counting_loads)

    # Act: one append from a cold instance.
    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="assistant", content="tail")

    # Assert: far fewer parses than records — the scan works from the tail.
    assert parsed["count"] < 10, (
        f"cold leaf scan parsed {parsed['count']} records for a 200-entry file"
    )


def test_cold_leaf_scan_does_not_read_text_the_whole_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold tip resolve must not Path.read_text the session JSONL."""
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    for i in range(50):
        storage.append_message(session.session_id, role="user", content=f"m{i}")

    real_read_text = Path.read_text

    def forbid_session_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".jsonl"):
            raise AssertionError("cold tip scan must not Path.read_text the session JSONL")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_session_read_text)

    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="assistant", content="tail")

    # Restore before asserting file contents (assert path uses read_text).
    monkeypatch.setattr(Path, "read_text", real_read_text)
    records = _records(session.session_id)
    assert records[-1]["parent_id"] == records[-2]["id"]


def test_cold_leaf_scan_reads_only_a_trailing_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large sessions must not pull the whole file into one read buffer."""
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    # Inflate past the default window so a full-file read would be obvious.
    padding = "x" * 2000
    for i in range(80):
        storage.append_message(session.session_id, role="user", content=f"m{i}-{padding}")

    path = session_path(session.session_id)
    file_size = path.stat().st_size
    assert file_size > jsonl_store._TAIL_SCAN_CHUNK_BYTES

    read_bytes = {"total": 0, "max_single": 0}
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        fh = real_open(self, *args, **kwargs)
        if self != path or "b" not in (args[0] if args else kwargs.get("mode", "r")):
            return fh
        orig_read = fh.read

        def tracked_read(size: int = -1) -> bytes:
            data = orig_read(size)
            read_bytes["total"] += len(data)
            read_bytes["max_single"] = max(read_bytes["max_single"], len(data))
            return data

        fh.read = tracked_read  # type: ignore[method-assign]
        return fh

    monkeypatch.setattr(Path, "open", counting_open)

    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="assistant", content="tail")

    assert read_bytes["max_single"] <= jsonl_store._TAIL_SCAN_CHUNK_BYTES, (
        f"single read was {read_bytes['max_single']} bytes — full file is {file_size}"
    )
    assert read_bytes["total"] <= jsonl_store._TAIL_SCAN_CHUNK_BYTES, (
        f"cold tip scan read {read_bytes['total']} bytes of a {file_size}-byte file"
    )
    monkeypatch.setattr(Path, "open", real_open)
    records = _records(session.session_id)
    assert records[-1]["parent_id"] == records[-2]["id"]


def test_cold_leaf_scan_skips_trailing_trace_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trailing diagnostic spans must not force a full-history parse."""
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")
    for i in range(5):
        storage.append_trace_span(
            session.session_id, span_kind="llm", name=f"span-{i}", duration_ms=1
        )

    parsed = {"count": 0}
    real_loads = json.loads

    def counting_loads(s: Any, *args: Any, **kwargs: Any) -> Any:
        parsed["count"] += 1
        return real_loads(s, *args, **kwargs)

    monkeypatch.setattr(jsonl_store.json, "loads", counting_loads)

    fresh = JsonlSessionStore()
    fresh.append_message(session.session_id, role="assistant", content="a")

    assert parsed["count"] < 10
    records = _records(session.session_id)
    question = next(r for r in records if r.get("role") == "user")
    answer = next(r for r in records if r.get("role") == "assistant")
    assert answer["parent_id"] == question["id"]
