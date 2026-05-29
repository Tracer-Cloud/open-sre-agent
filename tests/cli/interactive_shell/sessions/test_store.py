"""Tests for SessionStore: incremental writes (open_session, append_turn, flush, load_recent)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.sessions.store import SessionStore

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> ReplSession:
    return ReplSession()


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _patch_dir(tmp_path: Path):
    return patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path)


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


def test_append_turn_truncates_long_text(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.append_turn(session, "chat", "x" * 500)

    records = _read_lines(tmp_path / f"{session.session_id}.jsonl")
    turn = next(r for r in records if r["type"] == "turn")
    assert len(turn["text"]) == 200


def test_append_turn_noop_when_file_missing(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        # Do NOT call open_session — file doesn't exist
        SessionStore.append_turn(session, "chat", "hello")
    assert not list(tmp_path.glob("*.jsonl")), "no file should be created"


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


def test_flush_deletes_file_when_no_turns(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        SessionStore.flush(session)

    assert not (tmp_path / f"{session.session_id}.jsonl").exists()


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
        SessionStore.append_turn(session, "chat", "hi")
        SessionStore.append_turn(session, "alert", "OOM")
        # No flush — session still in progress

        results = SessionStore.load_recent()

    assert len(results) == 1
    assert results[0]["total_turns"] == 2
    assert results[0]["chat_turns"] == 1
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


# ── /reset lifecycle ──────────────────────────────────────────────────────────


def test_reset_closes_old_session_and_opens_new(tmp_path: Path) -> None:
    session = _make_session()
    with _patch_dir(tmp_path):
        SessionStore.open_session(session)
        session.record("chat", "pre-reset question")
        sid1 = session.session_id

        # Simulate /reset
        SessionStore.flush(session)
        session.clear()
        SessionStore.open_session(session)
        sid2 = session.session_id

        session.record("chat", "post-reset question")

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
