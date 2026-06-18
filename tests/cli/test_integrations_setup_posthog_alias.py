"""`opensre integrations <cmd> posthog` resolves to the `posthog_mcp` flow.

The bare ``posthog`` integration is env-configured analytics with no interactive
setup/verify flow, so management commands accept ``posthog`` as an alias for the
only real target, ``posthog_mcp``. Previously ``click.Choice`` rejected
``posthog`` with exit code 2 before any handler ran.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.integrations.cli import _HANDLERS, cmd_setup, cmd_verify


def test_setup_posthog_alias_resolves_to_posthog_mcp() -> None:
    runner = CliRunner()
    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_cmd,
        patch("app.integrations.cli.cmd_verify", return_value=0),
    ):
        mock_cmd.return_value = "posthog_mcp"
        result = runner.invoke(cli, ["integrations", "setup", "posthog"])
    assert result.exit_code == 0
    # The alias is resolved at the Click boundary, so cmd_setup receives the
    # canonical service name rather than the raw "posthog".
    mock_cmd.assert_called_once_with("posthog_mcp")


def test_setup_rejects_unknown_service() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["integrations", "setup", "not-a-real-service"])
    assert result.exit_code == 2
    assert "not one of" in result.output


def test_cmd_setup_posthog_alias_dispatches_posthog_mcp_handler(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Direct cmd_setup('posthog') (python -m app.integrations path) dispatches MCP."""
    called: list[str] = []
    monkeypatch.setitem(_HANDLERS, "posthog_mcp", lambda: called.append("posthog_mcp"))

    resolved = cmd_setup("posthog")

    assert resolved == "posthog_mcp"
    assert called == ["posthog_mcp"]
    assert "Setting up" in capsys.readouterr().out


def test_cmd_verify_posthog_alias_resolves_before_verifying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}

    def fake_verify(*, service: str | None, send_slack_test: bool = False) -> list[dict[str, str]]:
        captured["service"] = service
        return []

    monkeypatch.setattr("app.integrations.cli.verify_integrations", fake_verify)
    monkeypatch.setattr("app.integrations.cli.format_verification_results", lambda _results: "")
    monkeypatch.setattr(
        "app.integrations.cli.verification_exit_code",
        lambda *_args, **_kwargs: 0,
    )

    assert cmd_verify("posthog") == 0
    assert captured["service"] == "posthog_mcp"
