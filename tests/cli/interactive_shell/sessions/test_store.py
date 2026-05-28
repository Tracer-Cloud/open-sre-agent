"""Tests for SessionStore: flush and load_recent."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.sessions.store import SessionStore

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session(history: list[dict] | None = None) -> ReplSession:
    s = ReplSession()
    if history:
        s.history.extend(history)
    return s


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── flush ─────────────────────────────────────────────────────────────────────


def test_flush_skips_empty_session(tmp_path: Path) -> None:
    session = _make_session()
    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        SessionStore.flush(session)
    assert list(tmp_path.glob("*.jsonl")) == [], "no file written for empty session"


def test_flush_writes_correct_structure(tmp_path: Path) -> None:
    session = _make_session(
        [
            {"type": "chat", "text": "hello world", "ok": True},
            {"type": "alert", "text": "HighCPU on prod", "ok": True},
            {"type": "slash", "text": "/status", "ok": True},
        ]
    )
    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        SessionStore.flush(session)

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1

    records = _read_lines(files[0])
    assert records[0]["type"] == "session_start"
    assert records[0]["session_id"] == session.session_id

    turn_records = [r for r in records if r["type"] == "turn"]
    assert len(turn_records) == 3
    assert turn_records[0]["kind"] == "chat"
    assert turn_records[1]["kind"] == "alert"
    assert turn_records[2]["kind"] == "slash"

    end = records[-1]
    assert end["type"] == "session_end"
    assert end["total_turns"] == 3
    assert end["chat_turns"] == 1
    assert end["investigation_turns"] == 1


def test_flush_truncates_long_prompt(tmp_path: Path) -> None:
    long_text = "x" * 500
    session = _make_session([{"type": "chat", "text": long_text, "ok": True}])
    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        SessionStore.flush(session)

    files = list(tmp_path.glob("*.jsonl"))
    records = _read_lines(files[0])
    turn = next(r for r in records if r["type"] == "turn")
    assert len(turn["text"]) == 200


def test_flush_uses_session_session_id_as_filename(tmp_path: Path) -> None:
    session = _make_session([{"type": "chat", "text": "hi", "ok": True}])
    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        SessionStore.flush(session)

    expected_file = tmp_path / f"{session.session_id}.jsonl"
    assert expected_file.exists()


def test_flush_never_raises_on_bad_path() -> None:
    session = _make_session([{"type": "chat", "text": "hi", "ok": True}])
    with patch(
        "app.cli.interactive_shell.sessions.store._sessions_dir",
        return_value=Path("/nonexistent/cannot/write/here"),
    ):
        SessionStore.flush(session)  # must not raise


# ── load_recent ───────────────────────────────────────────────────────────────


def test_load_recent_returns_empty_when_no_dir(tmp_path: Path) -> None:
    missing = tmp_path / "no_sessions"
    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=missing):
        result = SessionStore.load_recent()
    assert result == []


def test_load_recent_returns_sessions_newest_first(tmp_path: Path) -> None:
    for started in ["2024-01-01T10:00:00+00:00", "2024-01-02T10:00:00+00:00"]:
        sid = str(uuid.uuid4())
        path = tmp_path / f"{sid}.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "session_start", "session_id": sid, "started_at": started}),
                    json.dumps({"type": "turn", "kind": "chat", "text": "hi"}),
                    json.dumps(
                        {
                            "type": "session_end",
                            "ended_at": started,
                            "duration_secs": 60,
                            "total_turns": 1,
                            "chat_turns": 1,
                            "investigation_turns": 0,
                        }
                    ),
                ]
            )
            + "\n"
        )

    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        results = SessionStore.load_recent()

    assert len(results) == 2
    assert results[0]["started_at"] > results[1]["started_at"]


def test_load_recent_handles_crashed_session(tmp_path: Path) -> None:
    sid = str(uuid.uuid4())
    path = tmp_path / f"{sid}.jsonl"
    path.write_text(
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

    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        results = SessionStore.load_recent()

    assert len(results) == 1
    assert results[0]["total_turns"] is None
    assert results[0]["duration_secs"] is None


def test_load_recent_skips_malformed_files(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text("this is not json\nalso not json\n")
    (tmp_path / "empty.jsonl").write_text("")

    sid = str(uuid.uuid4())
    path = tmp_path / f"{sid}.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": sid,
                        "started_at": "2024-01-03T10:00:00+00:00",
                    }
                ),
                json.dumps({"type": "turn", "kind": "chat", "text": "ok"}),
                json.dumps(
                    {
                        "type": "session_end",
                        "ended_at": "2024-01-03T10:01:00+00:00",
                        "duration_secs": 60,
                        "total_turns": 1,
                        "chat_turns": 1,
                        "investigation_turns": 0,
                    }
                ),
            ]
        )
        + "\n"
    )

    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        results = SessionStore.load_recent()

    assert len(results) == 1
    assert results[0]["session_id"] == sid


def test_load_recent_respects_n_limit(tmp_path: Path) -> None:
    for _ in range(5):
        sid = str(uuid.uuid4())
        path = tmp_path / f"{sid}.jsonl"
        path.write_text(
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
            + json.dumps(
                {
                    "type": "session_end",
                    "ended_at": "2024-01-01T10:01:00+00:00",
                    "duration_secs": 60,
                    "total_turns": 1,
                    "chat_turns": 1,
                    "investigation_turns": 0,
                }
            )
            + "\n"
        )

    with patch("app.cli.interactive_shell.sessions.store._sessions_dir", return_value=tmp_path):
        results = SessionStore.load_recent(n=3)

    assert len(results) == 3


# ── ReplSession session_id rotation ──────────────────────────────────────────


def test_repl_session_has_stable_session_id() -> None:
    s = ReplSession()
    assert isinstance(s.session_id, str)
    assert len(s.session_id) > 0
    assert s.started_at <= time.time()


def test_repl_session_rotates_id_on_clear() -> None:
    s = ReplSession()
    original_id = s.session_id
    original_started = s.started_at
    s.history.append({"type": "chat", "text": "hi", "ok": True})
    time.sleep(0.01)
    s.clear()
    assert s.session_id != original_id
    assert s.started_at >= original_started
