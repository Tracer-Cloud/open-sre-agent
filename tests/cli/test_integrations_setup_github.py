"""Tests for legacy `opensre integrations setup github` flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.__main__ import cli
from integrations.cli import _setup_github, cmd_setup
from integrations.github_mcp import GitHubMCPValidationResult


def _upsert_should_not_run(*_a: object, **_k: object) -> None:
    raise AssertionError("upsert_integration should not be called when validation fails")


def _mock_confirm(monkeypatch: pytest.MonkeyPatch, *, advanced: bool) -> None:
    """Mock the advanced-settings confirm prompt at the top of ``_setup_github``."""
    monkeypatch.setattr(
        "integrations.cli.questionary.confirm",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: advanced})(),
    )


def test_setup_github_prints_connected_and_saves_on_validation_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_setup_github validates before saving and prints identity + detail on success."""

    answers = iter(["https://api.githubcopilot.com/mcp/", "repos,issues"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.cli._p", fake_p)
    _mock_confirm(monkeypatch, advanced=True)
    monkeypatch.setattr("integrations.cli._setup_github_auth_token", lambda _mode: "ghp_x")
    monkeypatch.setattr("integrations.cli._prompt_github_repo_report_level", lambda: "full")
    monkeypatch.setattr(
        "integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )

    monkeypatch.setattr(
        "integrations.github_mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail=(
                "OK @devuser; repos=2; owners=Tracer-Cloud,acme; "
                "examples=Tracer-Cloud/opensre,acme/demo; mcp_tools=9"
            ),
            authenticated_user="devuser",
            repo_access_count=2,
            repo_access_scope_owners=("Tracer-Cloud", "acme"),
            repo_access_samples=("Tracer-Cloud/opensre", "acme/demo"),
        ),
    )

    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "integrations.cli.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )

    _setup_github()

    out = capsys.readouterr().out
    assert "Validating GitHub MCP integration" in out
    assert "Configuration validation: succeeded" in out
    assert "@devuser" in out
    assert "Repositories returned" in out
    assert "Tracer-Cloud/opensre" in out
    assert saved == [
        (
            "github",
            {
                "credentials": {
                    "mode": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "auth_token": "ghp_x",
                    "toolsets": ["repos", "issues"],
                    "username": "devuser",
                },
            },
        ),
    ]


def test_setup_github_simple_path_uses_hosted_defaults(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Declining advanced settings saves hosted defaults without transport/URL prompts."""

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        raise AssertionError("simple path should not call _p")

    def _no_level_prompt() -> str:
        raise AssertionError("simple path should not prompt for repo detail level")

    monkeypatch.setattr("integrations.cli._p", fake_p)
    _mock_confirm(monkeypatch, advanced=False)
    monkeypatch.setattr("integrations.cli._setup_github_auth_token", lambda _mode: "gho_browser")
    monkeypatch.setattr("integrations.cli._prompt_github_repo_report_level", _no_level_prompt)
    monkeypatch.setattr(
        "integrations.github_mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @u; repos=2; owners=acme; examples=acme/a; mcp_tools=5",
            authenticated_user="u",
            repo_access_count=2,
            repo_access_scope_owners=("acme",),
            repo_access_samples=("acme/a", "acme/b"),
            repo_access_probe_tool="list_starred_repositories",
        ),
    )

    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "integrations.cli.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )

    _setup_github()

    out = capsys.readouterr().out
    assert out.count("Validating GitHub MCP integration") == 1
    # Concise summary: no access-source / starred / repo enumeration noise.
    assert "Access source" not in out
    assert "Starred" not in out
    assert saved == [
        (
            "github",
            {
                "credentials": {
                    "mode": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "auth_token": "gho_browser",
                    "toolsets": ["repos", "issues", "pull_requests", "actions", "search"],
                    "username": "u",
                },
            },
        ),
    ]


def test_setup_github_exits_without_save_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.cli._p", fake_p)
    _mock_confirm(monkeypatch, advanced=True)
    monkeypatch.setattr("integrations.cli._setup_github_auth_token", lambda _mode: "")
    monkeypatch.setattr(
        "integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "integrations.github_mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="GitHub MCP connected, but authentication failed: bad token",
            failure_category="authentication",
        ),
    )
    monkeypatch.setattr("integrations.cli.upsert_integration", _upsert_should_not_run)

    with pytest.raises(SystemExit) as exc:
        _setup_github()
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Failure type:" in out
    assert "authentication failed" in out


def test_cmd_setup_github_skips_saved_line_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """cmd_setup must not print success/saved after a failed GitHub validation."""

    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.cli._p", fake_p)
    _mock_confirm(monkeypatch, advanced=True)
    monkeypatch.setattr("integrations.cli._setup_github_auth_token", lambda _mode: "x")
    monkeypatch.setattr(
        "integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "integrations.github_mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="validation failed for test",
            failure_category="connectivity",
        ),
    )
    monkeypatch.setattr("integrations.cli.upsert_integration", _upsert_should_not_run)

    with pytest.raises(SystemExit) as exc:
        cmd_setup("github")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Saved" not in out


def test_cmd_setup_github_prints_saved_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full cmd_setup('github') prints validation, Saved line, and does not duplicate handlers."""

    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.cli._p", fake_p)
    _mock_confirm(monkeypatch, advanced=True)
    monkeypatch.setattr("integrations.cli._setup_github_auth_token", lambda _mode: "tok")
    monkeypatch.setattr("integrations.cli._prompt_github_repo_report_level", lambda: "standard")
    monkeypatch.setattr(
        "integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "integrations.github_mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @u; repos=0; owners=-; examples=-; mcp_tools=5",
            authenticated_user="u",
            repo_access_count=0,
            repo_access_scope_owners=(),
            repo_access_samples=(),
        ),
    )
    monkeypatch.setattr("integrations.cli.upsert_integration", lambda *_a, **_k: None)

    cmd_setup("github")
    out = capsys.readouterr().out
    assert "Configuration validation: succeeded" in out
    assert "@u" in out
    assert "Saved" in out


def test_integrations_setup_github_cli_invokes_cmd_setup() -> None:
    runner = CliRunner()
    with (
        patch("cli.commands.integrations.capture_integration_setup_started"),
        patch("cli.commands.integrations.capture_integration_setup_completed"),
        patch("cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_cmd,
        patch("integrations.cli.cmd_verify", return_value=0),
    ):
        mock_cmd.return_value = "github"
        result = runner.invoke(cli, ["integrations", "setup", "github"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with("github")
