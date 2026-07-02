from __future__ import annotations

import pytest

from integrations.github import login as github_login
from integrations.github.identity import saved_github_username
from integrations.github.login import GitHubLoginResult
from integrations.github_mcp import GitHubMCPValidationResult
from integrations.github_mcp_oauth import GitHubDeviceToken


def test_authenticate_and_configure_github_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_login,
        "authorize_github_via_device_flow",
        lambda **_kwargs: GitHubDeviceToken(access_token="gho_token"),
    )
    monkeypatch.setattr(
        github_login,
        "validate_github_mcp_config",
        lambda _config: GitHubMCPValidationResult(
            ok=True, detail="OK", authenticated_user="octocat"
        ),
    )
    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        github_login,
        "upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )
    identified: list[str] = []
    monkeypatch.setattr(
        "platform.analytics.cli.identify_github_username",
        lambda username: identified.append(username),
    )

    result = github_login.authenticate_and_configure_github()

    assert result == GitHubLoginResult(ok=True, username="octocat", detail="OK")
    assert len(saved) == 1
    service, entry = saved[0]
    assert service == "github"
    credentials = entry["credentials"]
    assert isinstance(credentials, dict)
    assert credentials["auth_token"] == "gho_token"
    assert credentials["url"] == github_login.DEFAULT_GITHUB_MCP_URL
    assert credentials["mode"] == github_login.DEFAULT_GITHUB_MCP_MODE
    # The resolved GitHub login is persisted as a non-secret field so the
    # welcome banner can greet the user by their GitHub handle.
    assert credentials["username"] == "octocat"
    assert identified == ["octocat"]


def test_authenticate_and_configure_github_validation_failure_does_not_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        github_login,
        "authorize_github_via_device_flow",
        lambda **_kwargs: GitHubDeviceToken(access_token="gho_token"),
    )
    monkeypatch.setattr(
        github_login,
        "validate_github_mcp_config",
        lambda _config: GitHubMCPValidationResult(
            ok=False, detail="missing tools", authenticated_user="octocat"
        ),
    )
    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        github_login,
        "upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )

    result = github_login.authenticate_and_configure_github()

    assert result.ok is False
    assert result.username == "octocat"
    assert result.detail == "missing tools"
    assert saved == []


def test_saved_github_username_reads_integration_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.store.get_integration",
        lambda service: {"credentials": {"username": "octocat"}} if service == "github" else None,
    )

    assert saved_github_username() == "octocat"


def test_saved_github_username_empty_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.store.get_integration", lambda _service: None)

    assert saved_github_username() == ""
