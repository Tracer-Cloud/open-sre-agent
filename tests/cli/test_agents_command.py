"""Smoke tests for the ``opensre agents`` command group."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from app.agents.discovery import DiscoveredAgent
from app.agents.registry import AgentRecord, AgentRegistry
from app.cli.__main__ import cli
from app.cli.commands import agent as agent_cmd_mod


@pytest.fixture
def isolated_registry_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "agents.jsonl"

    def _registered_only(registry: AgentRegistry | None = None) -> list[AgentRecord]:
        return (registry or AgentRegistry(path=path)).list()

    monkeypatch.setattr(agent_cmd_mod, "AgentRegistry", lambda: AgentRegistry(path=path))
    monkeypatch.setattr(agent_cmd_mod, "registered_and_discovered_agents", _registered_only)
    return path


def test_agents_help_lists_all_subcommands() -> None:
    """``opensre agents --help`` must surface every subcommand."""
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "--help"])

    assert result.exit_code == 0, result.output
    for subcommand in ("list", "register", "forget", "scan", "watch"):
        assert subcommand in result.output, f"missing {subcommand!r} in help: {result.output}"
    assert "--all" in runner.invoke(cli, ["agents", "scan", "--help"]).output


def test_agents_list_renders_discovered_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cmd_mod,
        "registered_and_discovered_agents",
        lambda _registry=None: [
            AgentRecord(
                name="cursor-claude-code",
                pid=80435,
                command="claude --output-format stream-json",
                source="discovered",
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "list"])

    assert result.exit_code == 0, result.output
    assert "cursor-claude-code" in result.output
    assert "80435" in result.output
    assert "discovered" in result.output


def test_agents_register_list_and_forget(isolated_registry_path: Path) -> None:
    runner = CliRunner()

    register = runner.invoke(
        cli,
        ["agents", "register", "1234", "claude-code", "--command", "claude"],
    )
    listed = runner.invoke(cli, ["agents", "list"])
    forgotten = runner.invoke(cli, ["agents", "forget", "1234"])
    listed_again = runner.invoke(cli, ["agents", "list"])

    assert register.exit_code == 0, register.output
    assert "registered claude-code" in register.output
    assert listed.exit_code == 0, listed.output
    assert "1234" in listed.output
    assert "claude-code" in listed.output
    assert "claude" in listed.output
    assert forgotten.exit_code == 0, forgotten.output
    assert "forgot claude-code" in forgotten.output
    assert listed_again.exit_code == 0, listed_again.output
    assert "no agents discovered or registered yet" in listed_again.output
    assert AgentRegistry(path=isolated_registry_path).list() == []


def test_agents_scan_can_register_discovered_processes(
    isolated_registry_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        agent_cmd_mod,
        "discover_agent_processes",
        lambda **_kwargs: [DiscoveredAgent(name="claude-code-777", pid=777, command="claude code")],
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "scan", "--register"])

    assert result.exit_code == 0, result.output
    assert "agent scan" in result.output
    assert "pid" in result.output
    assert "agent" in result.output
    assert "command" in result.output
    assert "777" in result.output
    assert "claude-code-777" in result.output
    assert "claude code" in result.output
    assert "registered 1 agent(s)" in result.output
    assert AgentRegistry(path=isolated_registry_path).get(777) is not None


def test_agents_scan_truncates_long_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cmd_mod,
        "discover_agent_processes",
        lambda **_kwargs: [
            DiscoveredAgent(
                name="claude-code-777",
                pid=777,
                command="claude " + "--flag " * 40,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "scan"])

    assert result.exit_code == 0, result.output
    assert "--flag --flag" in result.output
    assert "..." in result.output or "…" in result.output
    assert "Next: run opensre agents scan --register to track 1 process(es)" in result.output


def test_agents_scan_all_passes_include_all(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[bool] = []

    def _discover(*, include_all: bool = False) -> list[DiscoveredAgent]:
        received.append(include_all)
        return [DiscoveredAgent(name="cursor-888", pid=888, command="Cursor Helper")]

    monkeypatch.setattr(agent_cmd_mod, "discover_agent_processes", _discover)
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "scan", "--all"])

    assert result.exit_code == 0, result.output
    assert received == [True]
    assert "showing helper processes" in result.output


def test_agents_scan_empty_state_mentions_all_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_cmd_mod, "discover_agent_processes", lambda **_kwargs: [])
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "scan"])

    assert result.exit_code == 0, result.output
    assert "no running AI-agent sessions detected" in result.output
    assert "opensre agents scan --all" in result.output


def test_agents_watch_reports_already_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_cmd_mod, "pid_exists", lambda _pid: False)
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "watch", "9876"])

    assert result.exit_code == 0, result.output
    assert "pid 9876 is not running" in result.output


def test_agents_group_registered_in_root_cli() -> None:
    """The group must be discoverable from the root help so other tooling
    (REPL command-completion, docs generation) can pick it up."""
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "agents" in result.output
