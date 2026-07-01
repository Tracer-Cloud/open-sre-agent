from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from surfaces.cli.__main__ import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_gateway_requires_telegram_subcommand(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway"])

    assert result.exit_code != 0
    assert "telegram" in result.output


def test_gateway_telegram_starts_gateway(runner: CliRunner) -> None:
    with patch("gateway.start_gateway.start_gateway") as mock_start:
        result = runner.invoke(cli, ["gateway", "telegram"])

    assert result.exit_code == 0
    mock_start.assert_called_once()
    assert "Telegram gateway" in result.output
