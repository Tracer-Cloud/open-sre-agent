"""Tests for SessionStore: incremental writes (open_session, append_turn, flush, load_recent)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.sessions.store import SessionStore, _sessions_dir

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> ReplSession:
    return ReplSession()


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _patch_dir(tmp_path: Path):
    return patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path)


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect OpenSRE's home to a real temp dir so SessionStore reads/writes real files.

    No mocking: ``_sessions_dir()`` resolves the real ``OPENSRE_HOME_DIR`` constant
    on every call, so pointing it at a temp directory exercises the genuine
    filesystem path end to end.
    """
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", tmp_path)
    return tmp_path


# ── open_session ──────────────────────────────────────────────────────────────


def test_open_session_creates_file_with_session_start(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    records = _read_lines(files[0])
    assert records[0]["type"] == "session_start"
    assert records[0]["session_id"] == session.session_id


def test_open_session_uses_session_id_as_filename(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
    assert (tmp_path / f"{session.session_id}.jsonl").exists()


def test_open_session_never_raises_on_bad_path() -> None:
    session = _make_session()
    with patch(
        "app.cli.interactive_shell.sessions.store._sessions_dir",
        return_value=Path("/nonexistent/cannot/write"),
    ):
        SessionStore.open_session(session)  # must not raise


# ── append_turn ───────────────────────────────────────────────────────────────


def test_append_turn_adds_record_to_existing_file(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hello world")
        SessionStore.append_turn(session, "alert", "HighCPU on prod")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    turns = [r for r in records if r["type"] == "turn"]
    assert len(turns) == 2
    assert turns[0] == {"type": "turn", "kind": "chat", "text": "hello world"}
    assert turns[1] == {"type": "turn", "kind": "alert", "text": "HighCPU on prod"}


def test_append_turn_stores_full_text_without_truncation(tmp_path: Path) -> None:
    session = _make_session()
    long_text = "x" * 500
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", long_text)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    turn = next(r for r in records if r["type"] == "turn")
    assert len(turn["text"]) == 500


def test_append_turn_noop_when_file_missing(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        # Do NOT call open_session — file doesn't exist
        SessionStore.append_turn(session, "chat", "hello")
    assert not list(tmp_path.glob("*.jsonl")), "no file should be created"


# ── append_turn_detail ────────────────────────────────────────────────────────


def test_append_turn_detail_writes_full_prompt_and_response(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn_detail(
            session.session_id,
            "chat",
            "how do I debug high CPU?",
            response="Root cause is a memory leak.",
            turn_id="abc-123",
            model="claude-3-5",
            provider="anthropic",
            latency_ms=1500,
        )

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    detail = next(r for r in records if r["type"] == "turn_detail")
    assert detail["kind"] == "chat"
    assert detail["prompt"] == "how do I debug high CPU?"
    assert detail["response"] == "Root cause is a memory leak."
    assert detail["turn_id"] == "abc-123"
    assert detail["model"] == "claude-3-5"
    assert detail["provider"] == "anthropic"
    assert detail["latency_ms"] == 1500


def test_append_turn_detail_omits_none_fields(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn_detail(session.session_id, "chat", "hi")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    detail = next(r for r in records if r["type"] == "turn_detail")
    assert "response" not in detail
    assert "turn_id" not in detail
    assert "model" not in detail


def test_append_turn_detail_noop_when_file_missing(tmp_path: Path) -> None:
    with _patch_dir(tmp_path):
        SessionStore.append_turn_detail("nonexistent-id", "chat", "hi")
    assert not list(tmp_path.glob("*.jsonl"))


# ── append_tool_call ──────────────────────────────────────────────────────────


def test_append_tool_call_writes_record(tmp_home: Path) -> None:
    session = _make_session()
    SessionStore.open_session(session)
    SessionStore.append_tool_call(
        session.session_id,
        tool="call_posthog_tool",
        arguments={"tool": "execute-sql", "args": {"query": "select 1"}},
        result='{"rows": []}',
        ok=True,
        source="posthog_mcp",
    )

    records = _read_lines(_sessions_dir() / f"{session.session_id}.jsonl")
    call = next(r for r in records if r["type"] == "tool_call")
    assert call["tool"] == "call_posthog_tool"
    assert call["arguments"] == {"tool": "execute-sql", "args": {"query": "select 1"}}
    assert call["result"] == '{"rows": []}'
    assert call["ok"] is True
    assert call["source"] == "posthog_mcp"
    assert "ts" in call


def test_append_tool_call_omits_source_when_none(tmp_home: Path) -> None:
    session = _make_session()
    SessionStore.open_session(session)
    SessionStore.append_tool_call(
        session.session_id,
        tool="list_posthog_tools",
        arguments={},
        result="error: boom",
        ok=False,
    )

    records = _read_lines(_sessions_dir() / f"{session.session_id}.jsonl")
    call = next(r for r in records if r["type"] == "tool_call")
    assert call["ok"] is False
    assert "source" not in call


def test_append_tool_call_noop_when_file_missing(tmp_home: Path) -> None:
    SessionStore.append_tool_call("nonexistent-id", tool="t", arguments={}, result="r", ok=True)
    assert not list(_sessions_dir().glob("*.jsonl"))


def test_append_tool_call_reopens_finalized_session(tmp_home: Path) -> None:
    session = _make_session()
    SessionStore.open_session(session)
    SessionStore.append_turn(session, "cli_agent", "events for davincios in posthog")
    SessionStore.flush(session)
    # A late tool-call write (e.g. background gather) must reopen the file.
    SessionStore.append_tool_call(
        session.session_id, tool="call_posthog_tool", arguments={}, result="{}", ok=True
    )

    records = _read_lines(_sessions_dir() / f"{session.session_id}.jsonl")
    assert any(r["type"] == "tool_call" for r in records)


# ── flush ─────────────────────────────────────────────────────────────────────


def test_flush_writes_session_end(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "q1")
        SessionStore.append_turn(session, "alert", "alert1")
        SessionStore.flush(session)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    end = records[-1]
    assert end["type"] == "session_end"
    assert end["total_turns"] == 2
    assert end["chat_turns"] == 1
    assert end["investigation_turns"] == 1


def test_flush_counts_cli_agent_turns_as_chat(tmp_path: Path) -> None:
    """execution.py records kind='cli_agent' for chat turns — must count as chat_turns."""
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "cli_agent", "why is redis slow?")
        SessionStore.append_turn(session, "cli_help", "how do I use /resume?")
        SessionStore.append_turn(session, "follow_up", "what else?")
        SessionStore.flush(session)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    end = records[-1]
    assert end["chat_turns"] == 3
    assert end["investigation_turns"] == 0


def test_flush_writes_conversation_snapshot_when_messages_present(tmp_path: Path) -> None:
    session = _make_session()
    session.cli_agent_messages = [("user", "hello"), ("assistant", "hi there")]
    session.accumulated_context = {"service": "api", "cluster": "prod"}
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hello")
        SessionStore.flush(session)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    snapshot = next((r for r in records if r["type"] == "conversation_snapshot"), None)
    assert snapshot is not None
    assert snapshot["cli_agent_messages"] == [["user", "hello"], ["assistant", "hi there"]]
    assert snapshot["accumulated_context"] == {"service": "api", "cluster": "prod"}
    # snapshot must come before session_end
    types = [r["type"] for r in records]
    assert types.index("conversation_snapshot") < types.index("session_end")


def test_flush_skips_snapshot_when_no_messages(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    assert not any(r["type"] == "conversation_snapshot" for r in records)


def test_flush_deletes_file_when_no_turns(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.flush(session)

    assert not (tmp_path / f"{session.session_id}.jsonl").exists()


def test_flush_keeps_file_when_only_turn_details(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        # turn_detail only (no stub) — should NOT delete the file
        SessionStore.append_turn_detail(session.session_id, "chat", "hello", response="hi")
        SessionStore.flush(session)

    assert (tmp_path / f"{session.session_id}.jsonl").exists()


def test_flush_noop_when_file_missing(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.flush(session)  # no open_session called — must not raise


def test_flush_is_idempotent(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)
        SessionStore.flush(session)  # second call must not append another session_end

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    end_records = [r for r in records if r["type"] == "session_end"]
    assert len(end_records) == 1, "flush() must be idempotent — only one session_end"


def test_append_turn_reopens_finalized_session(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "hello")
        SessionStore.flush(session)
        session.record("slash", "/status")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    types = [r["type"] for r in records]
    assert types.count("session_end") == 0
    assert records[-1] == {"type": "turn", "kind": "slash", "text": "/status"}


def test_reopen_session_strips_trailing_end_and_snapshot(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "hello")
        SessionStore.flush(session)
        SessionStore.reopen_session(session.session_id)
        session.record("chat", "continued")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    types = [r["type"] for r in records]
    assert types.count("session_end") == 0
    assert types.count("conversation_snapshot") == 0
    assert types[-1] == "turn"
    assert records[-1]["text"] == "continued"


def test_reopen_session_noop_for_open_session(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "hello")
        SessionStore.reopen_session(session.session_id)
        session.record("chat", "still open")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    assert records[-1]["text"] == "still open"
    assert all(r["type"] != "session_end" for r in records)


# ── session.record() wiring ───────────────────────────────────────────────────


def test_session_record_calls_append_turn(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "what's wrong with prod?")

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    turns = [r for r in records if r["type"] == "turn"]
    assert len(turns) == 1
    assert turns[0]["kind"] == "chat"
    assert turns[0]["text"] == "what's wrong with prod?"


# ── load_recent ───────────────────────────────────────────────────────────────


def test_load_recent_returns_empty_when_no_dir(tmp_path: Path) -> None:
    with _patch_dir(tmp_path / "missing"):
        result = SessionStore.load_recent()
    assert result == []


def test_load_recent_counts_turns_for_in_progress_session(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "cli_agent", "hi")
        SessionStore.append_turn(session, "chat", "follow-up")
        SessionStore.append_turn(session, "alert", "OOM")
        # No flush — session still in progress

        results = SessionStore.load_recent()

    assert len(results) == 1
    assert results[0]["total_turns"] == 3
    assert results[0]["chat_turns"] == 2
    assert results[0]["investigation_turns"] == 1
    assert results[0]["duration_secs"] is None
    assert results[0]["is_ended"] is False


def test_load_recent_uses_session_end_stats_when_available(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)

        results = SessionStore.load_recent()

    assert results[0]["is_ended"] is True
    assert results[0]["total_turns"] == 1
    assert results[0]["duration_secs"] is not None


def test_load_recent_reports_has_snapshot_true(tmp_path: Path) -> None:
    session = _make_session()
    session.cli_agent_messages = [("user", "hi"), ("assistant", "hello")]
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)

        results = SessionStore.load_recent()

    assert results[0]["has_snapshot"] is True


def test_load_recent_reports_has_snapshot_false_without_conversation(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)

        results = SessionStore.load_recent()

    assert results[0]["has_snapshot"] is False


def test_load_recent_returns_newest_first(tmp_path: Path) -> None:
    for started in ["2024-01-01T10:00:00+00:00", "2024-01-02T10:00:00+00:00"]:
        sid = str(uuid.uuid4())
        (tmp_path / f"{sid}.jsonl").write_text(
            json.dumps({"type": "session_start", "session_id": sid, "started_at": started})
            + "\n"
            + json.dumps({"type": "turn", "kind": "chat", "text": "hi"})
            + "\n"
        )

    with _patch_dir(tmp_path):
        results = SessionStore.load_recent()

    assert results[0]["started_at"] > results[1]["started_at"]


def test_load_recent_skips_malformed_files(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text("not json\n")
    (tmp_path / "empty.jsonl").write_text("")

    sid = str(uuid.uuid4())
    (tmp_path / f"{sid}.jsonl").write_text(
        json.dumps(
            {"type": "session_start", "session_id": sid, "started_at": "2024-01-01T10:00:00+00:00"}
        )
        + "\n"
        + json.dumps({"type": "turn", "kind": "chat", "text": "ok"})
        + "\n"
    )

    with _patch_dir(tmp_path):
        results = SessionStore.load_recent()

    assert len(results) == 1
    assert results[0]["session_id"] == sid


def test_load_recent_respects_n_limit(tmp_path: Path) -> None:
    for _ in range(5):
        sid = str(uuid.uuid4())
        (tmp_path / f"{sid}.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_start",
                    "session_id": sid,
                    "started_at": "2024-01-01T10:00:00+00:00",
                }
            )
            + "\n"
            + json.dumps({"type": "turn", "kind": "chat", "text": "hi"})
            + "\n"
        )

    with _patch_dir(tmp_path):
        assert len(SessionStore.load_recent(n=3)) == 3


# ── load_session ──────────────────────────────────────────────────────────────


def test_load_session_returns_none_for_missing_prefix(tmp_path: Path) -> None:
    with _patch_dir(tmp_path):
        assert SessionStore.load_session("nonexistent") is None


def test_load_session_returns_none_when_no_dir(tmp_path: Path) -> None:
    with _patch_dir(tmp_path / "missing"):
        assert SessionStore.load_session("abc") is None


def test_load_session_restores_from_conversation_snapshot(tmp_path: Path) -> None:
    session = _make_session()
    session.cli_agent_messages = [("user", "how is prod?"), ("assistant", "prod is healthy")]
    session.accumulated_context = {"service": "api"}
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "how is prod?")
        SessionStore.flush(session)

        data = SessionStore.load_session(session.session_id[:8])

    assert data is not None
    assert data["has_snapshot"] is True
    assert data["cli_agent_messages"] == [
        ("user", "how is prod?"),
        ("assistant", "prod is healthy"),
    ]
    assert data["accumulated_context"] == {"service": "api"}


def test_load_session_fallback_to_turn_details_when_no_snapshot(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn_detail(
            session.session_id, "chat", "debug high CPU", response="It's a leak"
        )
        # Flush without cli_agent_messages — no snapshot written
        SessionStore.flush(session)

        data = SessionStore.load_session(session.session_id[:8])

    assert data is not None
    assert data["has_snapshot"] is False
    messages = data["cli_agent_messages"]
    assert ("user", "debug high CPU") in messages
    assert ("assistant", "It's a leak") in messages


def test_load_session_ambiguous_prefix_returns_none(tmp_path: Path) -> None:
    # Two sessions sharing the same prefix
    for _ in range(2):
        sid = "aaaabbbb" + str(uuid.uuid4())[8:]
        (tmp_path / f"{sid}.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_start",
                    "session_id": sid,
                    "started_at": "2024-01-01T10:00:00+00:00",
                }
            )
            + "\n"
        )
    with _patch_dir(tmp_path):
        result = SessionStore.load_session("aaaa")
    assert result is None


def test_load_session_includes_history_and_turn_details(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hello")
        SessionStore.append_turn_detail(session.session_id, "chat", "hello", response="hi")
        SessionStore.flush(session)

        data = SessionStore.load_session(session.session_id)

    assert data is not None
    assert len(data["history"]) == 1
    assert data["history"][0]["kind"] == "chat"
    assert len(data["turn_details"]) == 1
    assert data["turn_details"][0]["response"] == "hi"


# ── session name derivation ───────────────────────────────────────────────────


def test_load_recent_derives_name_from_turn_detail(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "why is redis slow?")
        SessionStore.append_turn_detail(
            session.session_id, "chat", "why is redis slow?", response="It's a memory issue"
        )

        results = SessionStore.load_recent()

    assert results[0]["name"] == "why is redis slow?"


def test_load_recent_derives_name_from_cli_agent_turn(tmp_path: Path) -> None:
    """execution.py calls session.record('cli_agent', text) for real chat turns."""
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "cli_agent", "debug the OOM killer on prod")

        results = SessionStore.load_recent()

    assert results[0]["name"] == "debug the OOM killer on prod"


def test_load_recent_derives_name_from_turn_stub_when_no_detail(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "alert", "HighCPU on prod-api-1")

        results = SessionStore.load_recent()

    assert results[0]["name"] == "HighCPU on prod-api-1"


def test_load_recent_name_truncated_at_50_chars(tmp_path: Path) -> None:
    session = _make_session()
    long_prompt = "a" * 60
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", long_prompt)

        results = SessionStore.load_recent()

    assert results[0]["name"] == "a" * 50 + "…"


def test_load_recent_name_empty_for_slash_only_session(tmp_path: Path) -> None:
    sid = str(uuid.uuid4())
    (tmp_path / f"{sid}.jsonl").write_text(
        json.dumps(
            {"type": "session_start", "session_id": sid, "started_at": "2024-01-01T10:00:00+00:00"}
        )
        + "\n"
        + json.dumps({"type": "turn", "kind": "slash", "text": "/status"})
        + "\n"
    )
    with _patch_dir(tmp_path):
        results = SessionStore.load_recent()
    assert results[0]["name"] == ""


def test_load_session_includes_name(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "debug the OOM killer")
        SessionStore.flush(session)

        data = SessionStore.load_session(session.session_id[:8])

    assert data is not None
    assert data["name"] == "debug the OOM killer"


def test_count_prefix_matches_returns_correct_count(tmp_path: Path) -> None:
    for _ in range(3):
        sid = str(uuid.uuid4())
        (tmp_path / f"{sid}.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_start",
                    "session_id": sid,
                    "started_at": "2024-01-01T10:00:00+00:00",
                }
            )
            + "\n"
        )
    with _patch_dir(tmp_path):
        # Full UUID prefix matches exactly one
        first_sid = list(tmp_path.glob("*.jsonl"))[0].stem
        assert SessionStore.count_prefix_matches(first_sid[:8]) == 1
        # Very short prefix may match multiple — no assertion on count, just that it doesn't raise
        count = SessionStore.count_prefix_matches("")
        assert count == 3


def test_count_prefix_matches_returns_zero_for_missing_dir(tmp_path: Path) -> None:
    with _patch_dir(tmp_path / "missing"):
        assert SessionStore.count_prefix_matches("abc") == 0


def test_load_session_matches_full_id(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.flush(session)

        data = SessionStore.load_session(session.session_id)

    assert data is not None
    assert data["session_id"] == session.session_id


# ── flush resilience ─────────────────────────────────────────────────────────


def test_flush_writes_session_end_even_when_snapshot_serialization_fails(
    tmp_path: Path,
) -> None:
    """P1: a snapshot serialization failure must not prevent session_end from being written."""
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "hello")
        # Inject a non-JSON-serializable value into accumulated_context so
        # json.dumps(snapshot) raises TypeError inside the inner suppress block.
        session.accumulated_context["bad"] = object()  # type: ignore[assignment]

        SessionStore.flush(session)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    types = [r["type"] for r in records]
    # snapshot may be absent (serialization failed), but session_end must be present.
    assert "session_end" in types
    assert records[-1]["type"] == "session_end"


# ── /new lifecycle ────────────────────────────────────────────────────────────


def test_new_closes_old_session_and_opens_new(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "pre-new question")
        sid1 = session.session_id

        # Simulate /new (flush → clear → open_session)
        SessionStore.flush(session)
        session.clear()
        SessionStore.open_session(session)
        sid2 = session.session_id

        session.record("chat", "post-new question")

    assert sid1 != sid2
    # Old session file has session_end
    old_records = _read_lines(tmp_path / f"{sid1}.jsonl")
    assert old_records[-1]["type"] == "session_end"
    # New session file exists with turn but no session_end yet
    new_records = _read_lines(tmp_path / f"{sid2}.jsonl")
    assert new_records[0]["type"] == "session_start"
    assert any(r["type"] == "turn" for r in new_records)
    assert new_records[-1]["type"] != "session_end"


# ── ReplSession field behaviour ───────────────────────────────────────────────


def test_repl_session_has_stable_session_id() -> None:
    s = _make_session()
    assert isinstance(s.session_id, str) and len(s.session_id) > 0
    assert s.started_at <= time.time()


def test_repl_session_rotates_id_on_clear() -> None:
    s = _make_session()
    original_id = s.session_id
    s.history.append({"type": "chat", "text": "hi", "ok": True})
    time.sleep(0.01)
    s.clear()
    assert s.session_id != original_id
    assert s.started_at <= time.time()
