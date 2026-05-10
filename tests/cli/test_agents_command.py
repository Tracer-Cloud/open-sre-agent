"""Smoke tests for the ``opensre agents`` command group."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.agents.registry import AgentRecord
from app.cli.__main__ import cli
from app.cli.commands import agent as agent_cmd_mod


def test_agents_help_lists_all_subcommands() -> None:
    """``opensre agents --help`` must surface every placeholder subcommand."""
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "--help"])

    assert result.exit_code == 0, result.output
    for subcommand in ("list", "register", "forget"):
        assert subcommand in result.output, f"missing {subcommand!r} in help: {result.output}"


def test_agents_list_renders_discovered_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cmd_mod,
        "registered_and_discovered_agents",
        lambda: [
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


@pytest.mark.parametrize("subcommand", ["register", "forget"])
def test_agents_subcommand_prints_placeholder(subcommand: str) -> None:
    """Each stub subcommand must run successfully and print the placeholder."""
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", subcommand])

    assert result.exit_code == 0, result.output
    assert "not implemented yet" in result.output


def test_agents_group_registered_in_root_cli() -> None:
    """The group must be discoverable from the root help so other tooling
    (REPL command-completion, docs generation) can pick it up."""
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "agents" in result.output
