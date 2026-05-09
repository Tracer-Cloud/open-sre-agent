"""Unit tests for /agents slash command and conflict renderer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

from app.agents import config as config_mod
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


@pytest.fixture(autouse=True)
def isolated_agents_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Autouse: redirect ``agents_config_path()`` to a per-test tmp path so
    ``/agents`` (which now reads ``agents.yaml`` for the ``$/hr`` cell)
    and ``/agents budget`` never touch the developer's real
    ``~/.config/opensre/agents.yaml``.
    """
    target = tmp_path / "agents.yaml"
    monkeypatch.setattr(config_mod, "agents_config_path", lambda: target)
    return target


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

    def test_dollar_hr_cell_reads_from_agents_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))

        # Pre-seed the budget via the slash command itself so we exercise
        # the full write→read round-trip (set → list).
        session = ReplSession()
        write_console, _ = _capture()
        assert dispatch_slash("/agents budget claude-code 5", session, write_console) is True

        list_console, list_buf = _capture()
        assert dispatch_slash("/agents", session, list_console) is True
        assert "$5.00" in list_buf.getvalue()

    def test_bare_agents_does_not_crash_on_schema_invalid_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_agents_yaml: Path
    ) -> None:
        # Hand-edited agents.yaml with a typo'd field used to crash bare
        # /agents with a raw ValidationError traceback. The dashboard
        # must degrade gracefully (render with $/hr = '-') so the user
        # can still see their fleet while /agents budget surfaces the
        # actual error message.
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        isolated_agents_yaml.parent.mkdir(parents=True, exist_ok=True)
        isolated_agents_yaml.write_text(
            "agents:\n  claude-code:\n    hourly_budegt_usd: 5.0\n",
            encoding="utf-8",
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents", session, console) is True
        out = buf.getvalue()
        # Dashboard still renders the agent row.
        assert "claude-code" in out
        assert "8421" in out


class TestAgentsBudget:
    def test_no_args_empty_state_when_no_config(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget", session, console) is True
        assert "no per-agent budgets" in buf.getvalue().lower()

    def test_writes_and_round_trips_through_load(self, isolated_agents_yaml: Path) -> None:
        session = ReplSession()
        write_console, write_buf = _capture()
        assert dispatch_slash("/agents budget claude-code 5", session, write_console) is True

        # Confirmation message references the agent and amount.
        write_out = write_buf.getvalue()
        assert "claude-code" in write_out
        assert "$5.00" in write_out

        # Subsequent /agents budget lists the just-written entry.
        read_console, read_buf = _capture()
        assert dispatch_slash("/agents budget", session, read_console) is True
        read_out = read_buf.getvalue()
        assert "claude-code" in read_out
        assert "$5.00" in read_out

        # File on disk has the expected shape.
        assert isolated_agents_yaml.exists()

    def test_rejects_negative_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code -3", session, console) is True
        out = buf.getvalue()
        assert "invalid budget" in out.lower()
        # Latest slash invocation should be marked failed.
        assert session.history[-1]["ok"] is False

    def test_rejects_zero_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code 0", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_rejects_non_numeric_budget(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code five", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_rejects_nan_budget(self, isolated_agents_yaml: Path) -> None:
        # ``float("nan") <= 0`` is ``False``, so without ``math.isfinite``
        # ``nan`` would slip past the guard, hit set_agent_budget, and
        # poison agents.yaml so the next load raises ValidationError.
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code nan", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False
        # The file must not exist — a single non-finite write can't be
        # allowed to leave agents.yaml in an unreadable state.
        assert not isolated_agents_yaml.exists()

    def test_rejects_inf_budget(self, isolated_agents_yaml: Path) -> None:
        # ``float("inf") <= 0`` is ``False`` and ``gt=0`` alone accepts
        # ``inf`` (``inf > 0`` is ``True``); only ``isfinite`` blocks it.
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code inf", session, console) is True
        assert "invalid budget" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False
        assert not isolated_agents_yaml.exists()

    def test_single_arg_prints_usage(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget claude-code", session, console) is True
        assert "usage" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_first_arg_completions_include_budget(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "budget" in keywords

    def test_corrupt_config_surfaces_friendly_error(self, isolated_agents_yaml: Path) -> None:
        # Hand-edit an agents.yaml with a typo'd field. The loader
        # raises ValidationError; the slash handler catches it and
        # renders a "agents.yaml has invalid contents" message rather
        # than crashing the REPL.
        isolated_agents_yaml.parent.mkdir(parents=True, exist_ok=True)
        isolated_agents_yaml.write_text(
            "agents:\n  claude-code:\n    hourly_budegt_usd: 5.0\n",
            encoding="utf-8",
        )
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents budget", session, console) is True
        out = buf.getvalue()
        assert "invalid contents" in out.lower()
        assert session.history[-1]["ok"] is False


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
