from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.__main__ import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_gateway_uses_default_host_and_port(runner: CliRunner) -> None:
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli, ["gateway"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        "deployment.remote.server:app",
        host="127.0.0.1",
        port=2024,
        reload=False,
        log_level="info",
    )


def test_gateway_allows_host_and_port_overrides(runner: CliRunner) -> None:
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli, ["gateway", "--host", "0.0.0.0", "--port", "8080"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        "deployment.remote.server:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )


def test_gateway_sets_api_key_env(runner: CliRunner) -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENSRE_API_KEY", None)
        with patch("uvicorn.run"):
            result = runner.invoke(cli, ["gateway", "--api-key", "local-test-key"])

        assert result.exit_code == 0
        assert os.environ["OPENSRE_API_KEY"] == "local-test-key"


def test_gateway_sets_investigations_dir_env(runner: CliRunner) -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("INVESTIGATIONS_DIR", None)
        with patch("uvicorn.run"):
            result = runner.invoke(cli, ["gateway", "--investigations-dir", "/tmp/reports"])

        assert result.exit_code == 0
        assert os.environ["INVESTIGATIONS_DIR"] == "/tmp/reports"


def test_gateway_rejects_file_path_for_investigations_dir(runner: CliRunner) -> None:
    with runner.isolated_filesystem():
        with open("not-a-directory.txt", "w", encoding="utf-8") as handle:
            handle.write("placeholder")

        result = runner.invoke(
            cli,
            ["gateway", "--investigations-dir", "not-a-directory.txt"],
        )

    assert result.exit_code != 0
    assert "Directory" in result.output


def test_gateway_reload_flag(runner: CliRunner) -> None:
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli, ["gateway", "--reload"])

    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["reload"] is True
    assert "Auto-reload enabled" in result.output


def test_gateway_log_level_option(runner: CliRunner) -> None:
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli, ["gateway", "--log-level", "debug"])

    assert result.exit_code == 0
    assert mock_run.call_args.kwargs["log_level"] == "debug"


def test_gateway_rejects_invalid_port(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway", "--port", "70000"])

    assert result.exit_code != 0
    assert "70000 is not in the range 1<=x<=65535" in result.output


def test_gateway_rejects_invalid_log_level(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway", "--log-level", "verbose"])

    assert result.exit_code != 0
    assert "verbose" in result.output


def test_gateway_prints_startup_banner(runner: CliRunner) -> None:
    with patch("uvicorn.run"):
        result = runner.invoke(cli, ["gateway", "--host", "0.0.0.0", "--port", "9999"])

    assert result.exit_code == 0
    assert "http://0.0.0.0:9999" in result.output
