"""End-to-end /resume scenario tests: session identity, JSONL turns, slash recording."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from cli.interactive_shell.command_registry import dispatch_slash
from cli.interactive_shell.runtime.session import ReplSession
from cli.interactive_shell.sessions.store import SessionStore


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _read_turns(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("type") == "turn"
    ]


def _write_finalized_session(
    sessions_dir: Path,
    session_id: str,
    *,
    chat_text: str = "why is redis slow?",
    messages: list[tuple[str, str]] | None = None,
    context: dict[str, str] | None = None,
) -> Path:
    """Create a closed session file that /resume can load."""
    path = sessions_dir / f"{session_id}.jsonl"
    msgs = messages or [("user", chat_text), ("assistant", "check connection pool")]
    ctx = context or {"service": "redis"}
    lines = [
        json.dumps(
            {
                "type": "session_start",
                "session_id": session_id,
                "started_at": "2026-05-29T10:00:00+00:00",
            }
        ),
        json.dumps({"type": "turn", "kind": "chat", "text": chat_text}),
        json.dumps(
            {
                "type": "conversation_snapshot",
                "cli_agent_messages": [list(m) for m in msgs],
                "accumulated_context": ctx,
            }
        ),
        json.dumps({"type": "session_end", "total_turns": 1}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def isolated_sessions(tmp_path: Path) -> Path:
    """Sessions directory with SessionStore patched for the test."""
    directory = tmp_path / "sessions"
    directory.mkdir()
    with patch(
        "cli.interactive_shell.sessions.store._sessions_dir",
        return_value=directory,
    ):
        yield directory


def _open_current(session: ReplSession) -> None:
    SessionStore.open_session(session)


class TestResumeScenarioMatrix:
    """Scenario coverage for /resume session adoption and JSONL persistence."""

    def test_scenario_fresh_repl_resumes_prior_session(self, isolated_sessions: Path) -> None:
        """Fresh REPL session resumes a prior session: adopt ID, slash on target only."""
        target_id = "aaaa1111-2222-3333-4444-555566667777"
        _write_finalized_session(isolated_sessions, target_id, chat_text="why is redis slow?")

        session = ReplSession()
        current_id = session.session_id
        _open_current(session)

        console, buf = _capture()
        assert dispatch_slash(f"/resume {target_id[:8]}", session, console) is True

        assert session.session_id == target_id
        assert "resumed session" in buf.getvalue()

        current_path = isolated_sessions / f"{current_id}.jsonl"
        if current_path.exists():
            assert not any(
                t.get("kind") == "slash" and "/resume" in t.get("text", "")
                for t in _read_turns(current_path)
            )

        target_turns = _read_turns(isolated_sessions / f"{target_id}.jsonl")
        assert any(
            t.get("kind") == "slash" and t.get("text", "").startswith("/resume")
            for t in target_turns
        )
        assert session.cli_agent_messages[0] == ("user", "why is redis slow?")
        assert session.accumulated_context == {"service": "redis"}

    def test_scenario_post_resume_slash_and_chat_append_to_target(
        self,
        isolated_sessions: Path,
    ) -> None:
        """After /resume, further slash commands and chat turns land on the same file."""
        target_id = "bbbb2222-3333-4444-5555-666677778888"
        _write_finalized_session(isolated_sessions, target_id)

        session = ReplSession()
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)
        dispatch_slash("/status", session, console)
        session.record("chat", "follow-up question after resume")

        target_path = isolated_sessions / f"{target_id}.jsonl"
        kinds = [t["kind"] for t in _read_turns(target_path)]
        assert "slash" in kinds
        assert kinds.count("slash") >= 2
        assert "chat" in kinds
        assert (
            target_path.read_text(encoding="utf-8").strip().splitlines()[-1].find("session_end")
            == -1
        )

    def test_scenario_empty_starter_session_removed_on_resume(
        self,
        isolated_sessions: Path,
    ) -> None:
        """A brand-new REPL with no turns should not leave a junk file after /resume."""
        target_id = "cccc3333-4444-5555-6666-777788889999"
        _write_finalized_session(isolated_sessions, target_id)

        session = ReplSession()
        starter_id = session.session_id
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        starter_path = isolated_sessions / f"{starter_id}.jsonl"
        assert not starter_path.exists()
        assert session.session_id == target_id

    def test_scenario_resume_by_name_substring(self, isolated_sessions: Path) -> None:
        target_id = "dddd4444-5555-6666-7777-888899990000"
        _write_finalized_session(isolated_sessions, target_id, chat_text="investigate OOM killer")

        session = ReplSession()
        _open_current(session)
        console, buf = _capture()

        dispatch_slash("/resume OOM", session, console)

        assert session.session_id == target_id
        assert "resumed session" in buf.getvalue()
        target_turns = _read_turns(isolated_sessions / f"{target_id}.jsonl")
        assert any(t.get("text") == "/resume OOM" for t in target_turns)

    def test_scenario_resume_not_found_records_on_current(
        self,
        isolated_sessions: Path,
    ) -> None:
        session = ReplSession()
        current_id = session.session_id
        _open_current(session)
        console, buf = _capture()

        dispatch_slash("/resume deadbeef", session, console)

        assert session.session_id == current_id
        assert "not found" in buf.getvalue()
        turns = _read_turns(isolated_sessions / f"{current_id}.jsonl")
        assert turns[-1]["kind"] == "slash"
        assert turns[-1]["text"] == "/resume deadbeef"
        assert session.history[-1]["ok"] is False

    def test_scenario_resume_current_session_guard(
        self,
        isolated_sessions: Path,
    ) -> None:
        session = ReplSession()
        _open_current(session)
        session.record("chat", "still working here")
        console, buf = _capture()

        dispatch_slash(f"/resume {session.session_id[:8]}", session, console)

        assert "current session" in buf.getvalue()
        assert session.session_id  # unchanged
        turns = _read_turns(isolated_sessions / f"{session.session_id}.jsonl")
        assert any(t.get("text", "").startswith("/resume") for t in turns)

    def test_scenario_resume_empty_target_does_not_switch(
        self,
        isolated_sessions: Path,
    ) -> None:
        empty_id = "eeee5555-6666-7777-8888-999900001111"
        path = isolated_sessions / f"{empty_id}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "session_start",
                    "session_id": empty_id,
                    "started_at": "2026-05-29T10:00:00+00:00",
                }
            )
            + "\n"
            + json.dumps({"type": "session_end", "total_turns": 0})
            + "\n",
            encoding="utf-8",
        )

        session = ReplSession()
        current_id = session.session_id
        _open_current(session)
        console, buf = _capture()

        dispatch_slash(f"/resume {empty_id[:8]}", session, console)

        assert session.session_id == current_id
        assert "no conversation to resume" in buf.getvalue()

    def test_scenario_chain_resume_two_targets(
        self,
        isolated_sessions: Path,
    ) -> None:
        """Resume session A, then resume session B — each gets its own /resume slash turn."""
        id_a = "ffff6666-7777-8888-9999-000011112222"
        id_b = "11117777-8888-9999-0000-111122223333"
        _write_finalized_session(isolated_sessions, id_a, chat_text="session A question")
        _write_finalized_session(isolated_sessions, id_b, chat_text="session B question")

        session = ReplSession()
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {id_a[:8]}", session, console)
        assert session.session_id == id_a

        dispatch_slash(f"/resume {id_b[:8]}", session, console)
        assert session.session_id == id_b

        turns_a = _read_turns(isolated_sessions / f"{id_a}.jsonl")
        turns_b = _read_turns(isolated_sessions / f"{id_b}.jsonl")
        assert any("/resume" in t.get("text", "") for t in turns_a)
        assert any("/resume" in t.get("text", "") for t in turns_b)
        assert session.cli_agent_messages[0] == ("user", "session B question")

    def test_scenario_active_session_with_turns_flushed_without_resume_slash(
        self,
        isolated_sessions: Path,
    ) -> None:
        """Switching away from a session that had real turns preserves them without /resume."""
        target_id = "22228888-9999-0000-1111-222233334444"
        _write_finalized_session(isolated_sessions, target_id)

        session = ReplSession()
        current_id = session.session_id
        _open_current(session)
        session.record("chat", "work in progress")
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        old_turns = _read_turns(isolated_sessions / f"{current_id}.jsonl")
        assert any(t["kind"] == "chat" for t in old_turns)
        assert not any(t.get("kind") == "slash" for t in old_turns)
        assert (isolated_sessions / f"{current_id}.jsonl").read_text(encoding="utf-8").find(
            "session_end"
        ) != -1


@pytest.mark.integration
class TestResumeLiveRepl:
    """Live REPL smoke test via ReplDriver with isolated HOME."""

    def test_live_resume_round_trip(self, tmp_path: Path) -> None:
        from tests.utils.repl_driver import ReplDriver

        home = tmp_path / "home"
        home.mkdir()
        sessions_dir = home / ".opensre" / "sessions"
        sessions_dir.mkdir(parents=True)
        target_id = "live9999-aaaa-bbbb-cccc-ddddeeeeffff"
        _write_finalized_session(sessions_dir, target_id, chat_text="live redis investigation")

        with ReplDriver(home=home, startup_wait=10.0) as repl:
            if repl.contains("Press Enter to continue"):
                repl.send("", wait=3.0)
                repl.reset_output()

            repl.send("/sessions", wait=3.0)
            assert repl.contains("live9999") or repl.contains("live redis")

            repl.reset_output()
            repl.send(f"/resume {target_id[:8]}", wait=4.0)
            assert repl.contains("resumed session")
            assert repl.contains("live redis investigation")

            repl.reset_output()
            repl.send("/status", wait=2.0)
            assert repl.contains("interactions")

        target_path = sessions_dir / f"{target_id}.jsonl"
        assert target_path.exists()
        turns = _read_turns(target_path)
        assert any(t.get("kind") == "slash" and "/resume" in t.get("text", "") for t in turns)
        assert any(t.get("kind") == "slash" and "/status" in t.get("text", "") for t in turns)
