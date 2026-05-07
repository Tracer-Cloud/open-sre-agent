"""Unit tests for /agents slash command and conflict renderer."""

from __future__ import annotations

import io

from rich.console import Console
from rich.table import Table

from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    FileWriteConflict,
    render_conflicts,
)
from app.cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


class TestAgentsRegistration:
    def test_agents_command_is_registered(self) -> None:
        assert "/agents" in SLASH_COMMANDS

    def test_agents_first_arg_completions_include_conflicts(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "conflicts" in keywords

    def test_default_window_constant_is_ten_seconds(self) -> None:
        assert DEFAULT_WINDOW_SECONDS == 10.0


class TestAgentsDispatch:
    def test_conflicts_with_empty_event_source_renders_empty_state(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents conflicts", session, console) is True
        assert "no conflicts detected" in buf.getvalue()

    def test_no_subcommand_prints_usage_hint(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True
        out = buf.getvalue()
        assert "usage" in out.lower()
        assert "/agents conflicts" in out

    def test_unknown_subcommand_prints_error(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents bogus", session, console) is True
        out = buf.getvalue()
        assert "unknown subcommand" in out.lower()
        assert "bogus" in out


class TestRenderConflicts:
    def test_empty_list_returns_empty_state_string(self) -> None:
        assert render_conflicts([]) == "no conflicts detected"

    def test_non_empty_list_returns_table_with_paths_and_agents(self) -> None:
        conflicts = [
            FileWriteConflict(
                path="/repo/auth.py",
                agents=("claude-code:1", "cursor:2"),
                first_seen=100.0,
                last_seen=110.0,
            ),
        ]
        result = render_conflicts(conflicts)
        assert isinstance(result, Table)

        buf = io.StringIO()
        Console(file=buf, force_terminal=False, highlight=False, width=120).print(result)
        out = buf.getvalue()
        assert "/repo/auth.py" in out
        assert "claude-code:1" in out
        assert "cursor:2" in out

    def test_multiple_conflicts_each_rendered(self) -> None:
        conflicts = [
            FileWriteConflict(
                path="/new.py",
                agents=("claude-code:1", "cursor:2"),
                first_seen=140.0,
                last_seen=150.0,
            ),
            FileWriteConflict(
                path="/old.py",
                agents=("aider:3", "cursor:2"),
                first_seen=100.0,
                last_seen=105.0,
            ),
        ]
        result = render_conflicts(conflicts)
        assert isinstance(result, Table)

        buf = io.StringIO()
        Console(file=buf, force_terminal=False, highlight=False, width=120).print(result)
        out = buf.getvalue()
        assert "/new.py" in out
        assert "/old.py" in out
        assert "aider:3" in out
