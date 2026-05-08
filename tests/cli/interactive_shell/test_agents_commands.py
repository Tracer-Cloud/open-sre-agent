"""Unit tests for /agents slash command and conflict renderer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    FileWriteConflict,
    render_conflicts,
)
from app.agents.registry import AgentRecord, AgentRegistry
from app.cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _isolate_registry(monkeypatch: pytest.MonkeyPatch, path: Path) -> AgentRegistry:
    """Point the slash command's ``AgentRegistry()`` constructor at
    ``path`` so tests don't read the developer's real
    ``~/.config/opensre/agents.jsonl``. Returns the registry instance
    that the test can populate.
    """
    registry = AgentRegistry(path=path)

    from app.cli.interactive_shell.command_registry import agents as agents_mod

    monkeypatch.setattr(agents_mod, "AgentRegistry", lambda: AgentRegistry(path=path))
    return registry


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

    def test_no_subcommand_with_empty_registry_renders_empty_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/agents", session, console) is True

        out = buf.getvalue()
        # Caption from agents_view.render_agents_table:
        assert "no agents registered" in out
        # Header row still rendered with the dashboard column structure:
        assert "agent" in out
        assert "pid" in out

    def test_no_subcommand_renders_registered_agents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        registry.register(AgentRecord(name="cursor-tab", pid=9133, command="cursor"))

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True

        out = buf.getvalue()
        assert "claude-code" in out
        assert "8421" in out
        assert "cursor-tab" in out
        assert "9133" in out

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
