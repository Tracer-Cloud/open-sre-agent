"""Smoke tests for the ``opensre agents`` command group (issue #1486).

Phase-0 of the ``monitor-local-agents`` initiative: ensure the new top-level
group is reachable and each placeholder subcommand exits cleanly. Real
behavior is added by later tickets in the same series.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli


def test_agents_help_lists_all_subcommands() -> None:
    """``opensre agents --help`` must surface every placeholder subcommand."""
    runner = CliRunner()

    result = runner.invoke(cli, ["agents", "--help"])

    assert result.exit_code == 0, result.output
    for subcommand in ("list", "register", "forget"):
        assert subcommand in result.output, f"missing {subcommand!r} in help: {result.output}"


@pytest.mark.parametrize("subcommand", ["list", "register", "forget"])
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
