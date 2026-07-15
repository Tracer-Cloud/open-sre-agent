"""PostHog REST and MCP integrations use distinct CLI service names.

Bare ``posthog`` is the REST credentials integration (setup + verify).
``posthog_mcp`` is the separate MCP setup/verify flow — matching the Sentry /
``sentry_mcp`` split.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from integrations.cli import _HANDLERS, cmd_setup, cmd_verify
from surfaces.cli.__main__ import cli


def test_setup_posthog_dispatches_rest_handler() -> None:
    runner = CliRunner()
    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_cmd,
        patch("integrations.cli.cmd_verify", return_value=0),
    ):
        mock_cmd.return_value = "posthog"
        result = runner.invoke(cli, ["integrations", "setup", "posthog"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with("posthog")


def test_setup_posthog_mcp_still_works() -> None:
    runner = CliRunner()
    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_cmd,
        patch("integrations.cli.cmd_verify", return_value=0),
    ):
        mock_cmd.return_value = "posthog_mcp"
        result = runner.invoke(cli, ["integrations", "setup", "posthog_mcp"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with("posthog_mcp")


def test_setup_rejects_unknown_service() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["integrations", "setup", "not-a-real-service"])
    assert result.exit_code == 2
    assert "not one of" in result.output


def test_cmd_setup_posthog_dispatches_rest_handler(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[str] = []
    monkeypatch.setitem(_HANDLERS, "posthog", lambda: called.append("posthog"))

    resolved = cmd_setup("posthog")

    assert resolved == "posthog"
    assert called == ["posthog"]
    assert "Setting up" in capsys.readouterr().out


def test_cmd_setup_posthog_mcp_dispatches_handler(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[str] = []
    monkeypatch.setitem(_HANDLERS, "posthog_mcp", lambda: called.append("posthog_mcp"))

    resolved = cmd_setup("posthog_mcp")

    assert resolved == "posthog_mcp"
    assert called == ["posthog_mcp"]
    assert "Setting up" in capsys.readouterr().out


def test_cmd_verify_posthog_resolves_to_rest_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}

    def fake_verify(*, service: str | None, send_slack_test: bool = False) -> list[dict[str, str]]:
        captured["service"] = service
        return []

    monkeypatch.setattr("integrations.cli.verify_integrations", fake_verify)
    monkeypatch.setattr("integrations.cli.format_verification_results", lambda _results: "")
    monkeypatch.setattr(
        "integrations.cli.verification_exit_code",
        lambda *_args, **_kwargs: 0,
    )

    assert cmd_verify("posthog") == 0
    assert captured["service"] == "posthog"


def test_cmd_verify_posthog_mcp_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}

    def fake_verify(*, service: str | None, send_slack_test: bool = False) -> list[dict[str, str]]:
        captured["service"] = service
        return []

    monkeypatch.setattr("integrations.cli.verify_integrations", fake_verify)
    monkeypatch.setattr("integrations.cli.format_verification_results", lambda _results: "")
    monkeypatch.setattr(
        "integrations.cli.verification_exit_code",
        lambda *_args, **_kwargs: 0,
    )

    assert cmd_verify("posthog_mcp") == 0
    assert captured["service"] == "posthog_mcp"
