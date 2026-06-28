"""Tests for the in-memory session storage backend."""

from __future__ import annotations

from context.session import InMemorySessionStorage, ReplSession


def _session(storage: InMemorySessionStorage) -> ReplSession:
    return ReplSession(storage=storage)


def test_open_then_record_appends_turn() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "hello world")

    records = storage.read(session.session_id)
    assert records[0]["type"] == "session"
    assert records[0]["version"] == 2
    turns = [r for r in records if r["type"] == "custom_message"]
    assert turns[0]["custom_type"] == "turn_stub"
    assert turns[0]["kind"] == "chat"
    assert turns[0]["text"] == "hello world"


def test_record_noop_when_not_opened() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    session.record("chat", "hi")  # no open_session
    assert storage.read(session.session_id) == []


def test_flush_writes_session_end_with_counts() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "q1")
    session.record("alert", "boom")
    storage.flush(session)

    leaf = storage.read(session.session_id)[-1]
    assert leaf["type"] == "leaf"
    assert leaf["total_turns"] == 2


def test_flush_deletes_empty_session() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    storage.flush(session)
    assert storage.read(session.session_id) == []


def test_flush_is_idempotent() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "hi")
    storage.flush(session)
    storage.flush(session)
    leaves = [r for r in storage.read(session.session_id) if r["type"] == "leaf"]
    assert len(leaves) == 1


def test_append_turn_detail_writes_message_entries() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    storage.append_turn_detail(session.session_id, "chat", "hello", response="hi")

    records = storage.read(session.session_id)
    messages = [r for r in records if r["type"] == "message"]
    assert [(r["role"], r["content"]) for r in messages] == [("user", "hello"), ("assistant", "hi")]


def test_append_tool_call_reopens_finalized_session() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    session.record("chat", "do a thing")
    storage.flush(session)
    storage.append_tool_call(session.session_id, tool="t", arguments={}, result="{}", ok=True)

    records = storage.read(session.session_id)
    assert any(r["type"] == "tool_call" for r in records)
    assert any(r["type"] == "tool_result" for r in records)


def test_append_investigation_result_returns_id() -> None:
    storage = InMemorySessionStorage()
    session = _session(storage)
    storage.open_session(session)
    inv_id = storage.append_investigation_result(
        session.session_id, {"root_cause": "leak", "problem_md": "report"}, trigger="t"
    )
    inv = next(r for r in storage.read(session.session_id) if r["type"] == "investigation_result")
    assert inv["investigation_id"] == inv_id
    assert inv["root_cause"] == "leak"
