from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.cli.__main__ import cli
from app.integrations.cli import _HANDLERS, _setup_openclaw, _setup_vercel


def test_integrations_show_redacts_api_token() -> None:
    runner = CliRunner()

    with patch(
        "app.integrations.cli.get_integration",
        return_value={
            "id": "vercel-1234",
            "service": "vercel",
            "status": "active",
            "credentials": {
                "api_token": "vcp_sensitive_token_value",
                "team_id": "team_123",
            },
        },
    ):
        result = runner.invoke(cli, ["integrations", "show", "vercel"])

    assert result.exit_code == 0
    assert "vcp_****" in result.output
    assert "vcp_sensitive_token_value" not in result.output


def test_integrations_setup_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "github"
        result = runner.invoke(cli, ["integrations", "setup", "github"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("github")
    mock_verify.assert_called_once_with("github")


def test_integrations_setup_accepts_vercel() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        mock_setup.return_value = "vercel"
        result = runner.invoke(cli, ["integrations", "setup", "vercel"])

    assert result.exit_code == 1
    mock_setup.assert_called_once_with("vercel")
    mock_verify.assert_called_once_with("vercel")
    mock_capture.assert_not_called()


def test_integrations_setup_accepts_openclaw() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        mock_setup.return_value = "openclaw"
        result = runner.invoke(cli, ["integrations", "setup", "openclaw"])

    assert result.exit_code == 1
    mock_setup.assert_called_once_with("openclaw")
    mock_verify.assert_called_once_with("openclaw")
    mock_capture.assert_not_called()


def test_setup_vercel_saves_credentials(monkeypatch) -> None:
    answers = iter(["vcp_test_token", "team_123"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr(
        "app.integrations.cli.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )

    _setup_vercel()

    assert _HANDLERS["vercel"] is _setup_vercel
    assert saved == [
        (
            "vercel",
            {"credentials": {"api_token": "vcp_test_token", "team_id": "team_123"}},
        )
    ]


def test_setup_openclaw_saves_credentials(monkeypatch) -> None:
    answers = iter(["1", "openclaw", "mcp serve"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr(
        "app.integrations.cli.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )
    monkeypatch.setattr(
        "app.integrations.cli.validate_openclaw_config",
        lambda _config: type("Result", (), {"ok": True, "detail": "ok"})(),
    )

    _setup_openclaw()

    assert _HANDLERS["openclaw"] is _setup_openclaw
    assert saved == [
        (
            "openclaw",
            {
                "credentials": {
                    "mode": "stdio",
                    "command": "openclaw",
                    "args": ["mcp", "serve"],
                    "url": "",
                    "auth_token": "",
                }
            },
        )
    ]


def test_integrations_setup_skips_auto_verify_for_unverifiable_service() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify") as mock_verify,
    ):
        # rds is registered in SETUP_SERVICES but intentionally absent from
        # VERIFY_SERVICES, so it exercises the auto-verify-skip path.
        # (opensearch was used here previously but moved into VERIFY_SERVICES
        # by PR #1143, which is why this assertion was updated.)
        mock_setup.return_value = "rds"
        result = runner.invoke(cli, ["integrations", "setup", "rds"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("rds")
    mock_verify.assert_not_called()


def test_integrations_verify_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "github"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "github",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("github")


def test_integrations_verify_accepts_openclaw() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "openclaw"])

    assert result.exit_code == 1
    mock_verify.assert_called_once_with(
        "openclaw",
        send_slack_test=False,
    )
    mock_capture.assert_not_called()


def test_integrations_verify_accepts_argocd() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "argocd"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "argocd",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("argocd")
