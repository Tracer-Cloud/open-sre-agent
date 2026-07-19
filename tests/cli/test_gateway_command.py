from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from surfaces.cli.__main__ import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_gateway_requires_subcommand(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway"])

    assert result.exit_code != 0
    for subcommand in ("start", "stop", "status", "logs"):
        assert subcommand in result.output


def test_gateway_start_foreground_runs_manager(runner: CliRunner) -> None:
    with patch("surfaces.cli.gateway_entry.start_gateway") as mock_start:
        result = runner.invoke(cli, ["gateway", "start", "--foreground"])

    assert result.exit_code == 0
    mock_start.assert_called_once()
    assert "OpenSRE gateway" in result.output


def test_gateway_entry_wires_slash_ports() -> None:
    """Production entry must inject headless slash ports into GatewayManager."""
    mock_manager = MagicMock()
    with patch("gateway.runtime.manager.GatewayManager", return_value=mock_manager) as ctor:
        from surfaces.cli.gateway_entry import main as entry_main

        entry_main()

    assert ctor.call_args.kwargs["slash_ports_factory"].__name__ == "gateway_slash_ports_factory"
    mock_manager.start_gateway.assert_called_once_with(wait=True)


def test_gateway_start_spawns_daemon(runner: CliRunner) -> None:
    with patch(
        "surfaces.cli.commands.gateway.start_gateway_daemon",
        return_value=(True, "Telegram gateway started (pid 4242)."),
    ) as mock_start:
        result = runner.invoke(cli, ["gateway", "start"])

    assert result.exit_code == 0
    mock_start.assert_called_once()
    assert "pid 4242" in result.output
    assert "Logs:" in result.output


def test_gateway_status_reports_stopped(runner: CliRunner) -> None:
    with patch("surfaces.cli.commands.gateway.gateway_daemon_pid", return_value=None):
        result = runner.invoke(cli, ["gateway", "status"])

    assert result.exit_code == 0
    assert "stopped" in result.output


def test_gateway_stop_reports_result(runner: CliRunner) -> None:
    with patch(
        "surfaces.cli.commands.gateway.stop_gateway_daemon",
        return_value=(True, "Telegram gateway stopped (pid 4242)."),
    ):
        result = runner.invoke(cli, ["gateway", "stop"])

    assert result.exit_code == 0
    assert "stopped" in result.output
