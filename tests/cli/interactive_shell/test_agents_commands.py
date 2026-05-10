"""Unit tests for /agents slash command and conflict renderer."""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

from app.agents import blast_radius as blast_radius_module
from app.agents import config as config_mod
from app.agents.blast_radius import BlastRadiusEvent
from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    FileWriteConflict,
    WriteEvent,
    render_conflicts,
)
from app.agents.network_egress import NetworkEgressEvent
from app.agents.probe import ProcessSnapshot
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.sudo_invocations import SudoInvocationEvent
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


@pytest.fixture(autouse=True)
def _isolated_blast_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse: stub :func:`collect_recent_write_events` so ``/agents conflicts``
    in this test module never triggers a real ``watchdog`` observer.

    The slash command lazy-starts an observer per registered agent on
    first call (#1500). Without this stub, tests that don't isolate the
    registry would attempt to read ``cwd`` of live processes on the
    developer's machine and start filesystem watchers on real
    directories. Tests that need the integration path (e.g. asserting
    events flow through to ``detect_conflicts``) override this stub via
    a tighter ``monkeypatch.setattr`` of their own.
    """
    from app.cli.interactive_shell.command_registry import agents as agents_mod

    monkeypatch.setattr(agents_mod, "collect_recent_write_events", lambda *_args, **_kwargs: [])
    blast_radius_module._reset_watchers_for_tests()


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

    def test_conflicts_renders_collisions_when_blast_radius_yields_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin the integration: when the blast-radius coordinator returns
        :class:`WriteEvent` records for two distinct agents writing the
        same path within the window, ``/agents conflicts`` must render
        the resulting collision through ``detect_conflicts``.
        """
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))
        registry.register(AgentRecord(name="cursor-tab", pid=9133, command="cursor"))

        # Override the autouse stub: feed two colliding writes anchored to
        # the most recent event so detect_conflicts' anchor-based window
        # keeps both inside.
        from app.cli.interactive_shell.command_registry import agents as agents_mod

        anchor = time.time()
        events = [
            WriteEvent(agent="claude-code:8421", path="/repo/auth.py", timestamp=anchor - 1.0),
            WriteEvent(agent="cursor-tab:9133", path="/repo/auth.py", timestamp=anchor),
        ]
        monkeypatch.setattr(
            agents_mod,
            "collect_recent_write_events",
            lambda *_args, **_kwargs: events,
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents conflicts", session, console) is True

        out = buf.getvalue()
        assert "/repo/auth.py" in out
        assert "claude-code:8421" in out
        assert "cursor-tab:9133" in out

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


class TestAgentsInspect:
    """``/agents inspect <pid>`` — three-section Blast radius panel.

    The handler lazy-starts three watchers; tests stub the three
    coordinator functions so no real psutil polling threads spin up.
    """

    def _stub_coordinators(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        write_events: list[BlastRadiusEvent],
        sudo_events: list[SudoInvocationEvent],
        egress_events: list[NetworkEgressEvent],
        project_root: str | None = "/repo",
        started_at: datetime | None = None,
    ) -> None:
        from app.cli.interactive_shell.command_registry import agents as agents_mod

        monkeypatch.setattr(
            agents_mod, "collect_recent_outside_writes", lambda *_a, **_k: write_events
        )
        monkeypatch.setattr(agents_mod, "collect_recent_sudo_events", lambda *_a, **_k: sudo_events)
        monkeypatch.setattr(
            agents_mod, "collect_recent_egress_events", lambda *_a, **_k: egress_events
        )
        monkeypatch.setattr(agents_mod, "_project_root_for_inspect", lambda _record: project_root)
        # ``probe`` returns a ProcessSnapshot or None; the handler only
        # uses ``.started_at``, so a synthetic snapshot is enough.
        snap = (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=0.0,
                rss_mb=1.0,
                num_fds=10,
                num_connections=2,
                status="running",
                started_at=started_at,
            )
            if started_at is not None
            else None
        )
        monkeypatch.setattr(agents_mod, "probe", lambda *_a, **_k: snap)

    def test_usage_message_when_no_pid_given(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents inspect", session, console) is True
        assert "usage" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_invalid_pid_is_rejected(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents inspect notapid", session, console) is True
        assert "invalid pid" in buf.getvalue().lower()
        assert session.history[-1]["ok"] is False

    def test_unknown_pid_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")  # empty registry
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents inspect 12345", session, console) is True
        out = buf.getvalue()
        assert "no registered agent" in out.lower()
        assert session.history[-1]["ok"] is False

    def test_renders_panel_with_all_three_sections_when_events_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))

        self._stub_coordinators(
            monkeypatch,
            write_events=[
                BlastRadiusEvent(
                    agent="claude-code:8421",
                    path="/etc/danger.conf",
                    timestamp=100.0,
                    outside_project_root=True,
                )
            ],
            sudo_events=[
                SudoInvocationEvent(
                    agent="claude-code:8421",
                    command="sudo apt update",
                    child_pid=9911,
                    timestamp=101.0,
                )
            ],
            egress_events=[
                NetworkEgressEvent(
                    agent="claude-code:8421",
                    remote_host="1.2.3.4",
                    remote_port=443,
                    family="ipv4",
                    timestamp=102.0,
                )
            ],
            started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents inspect 8421", session, console) is True

        out = buf.getvalue()
        # Panel title and header lines
        assert "Blast radius" in out
        assert "claude-code" in out
        assert "8421" in out
        # Each of the three sections renders its evidence row
        assert "/etc/danger.conf" in out
        assert "sudo apt update" in out
        assert "1.2.3.4" in out
        assert "443" in out
        # Header carries the lazy-start caveat verbatim
        assert "lazy-start" in out

    def test_renders_empty_section_captions_when_streams_are_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _isolate_registry(monkeypatch, tmp_path / "agents.jsonl")
        registry.register(AgentRecord(name="claude-code", pid=8421, command="claude"))

        self._stub_coordinators(
            monkeypatch,
            write_events=[],
            sudo_events=[],
            egress_events=[],
            project_root=None,
            started_at=None,
        )

        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/agents inspect 8421", session, console) is True

        out = buf.getvalue()
        assert "Blast radius" in out
        # Empty-state caption from blast_radius_view._EMPTY_SECTION
        assert "nothing observed yet" in out

    def test_first_arg_completions_include_inspect(self) -> None:
        cmd = SLASH_COMMANDS["/agents"]
        keywords = [pair[0] for pair in cmd.first_arg_completions]
        assert "inspect" in keywords
