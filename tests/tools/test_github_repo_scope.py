"""Tests for GitHub repository scope inference helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from integrations.github.mcp import GitHubMCPConfig
from integrations.github.repo_scope import (
    apply_github_repo_scope,
    detect_git_remote_repo_scope,
    infer_github_repo_scope,
    parse_github_repository_reference,
    split_repo_full_name,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/Tracer-Cloud/opensre", ("Tracer-Cloud", "opensre")),
        ("https://github.com/Tracer-Cloud/opensre.git", ("Tracer-Cloud", "opensre")),
        (
            "https://github.com/Tracer-Cloud/opensre/issues/42",
            ("Tracer-Cloud", "opensre"),
        ),
        ("github.com/org/my-repo", ("org", "my-repo")),
        ("repo:Tracer-Cloud/opensre windows crash", ("Tracer-Cloud", "opensre")),
        ("see Tracer-Cloud/opensre for details", ("Tracer-Cloud", "opensre")),
    ],
)
def test_parse_github_repository_reference(text: str, expected: tuple[str, str]) -> None:
    assert parse_github_repository_reference(text) == expected


def test_parse_github_repository_reference_last_match_wins() -> None:
    text = "https://github.com/first/a and https://github.com/second/b"
    assert parse_github_repository_reference(text) == ("second", "b")


def test_parse_github_repository_reference_rejects_paths() -> None:
    assert parse_github_repository_reference("cli/interactive_shell/chat") is None


def test_split_repo_full_name() -> None:
    assert split_repo_full_name("Tracer-Cloud/opensre.git") == ("Tracer-Cloud", "opensre")
    assert split_repo_full_name("owner-only") == ("", "")


def test_infer_github_repo_scope_prefers_current_message_over_cache() -> None:
    scope = infer_github_repo_scope(
        message="issues in https://github.com/new-org/new-repo",
        conversation_messages=[],
        cached=("old-org", "old-repo"),
    )
    assert scope == ("new-org", "new-repo")


def test_infer_github_repo_scope_uses_conversation_before_cache() -> None:
    scope = infer_github_repo_scope(
        message="do these searches",
        conversation_messages=[
            ("user", "https://github.com/Tracer-Cloud/opensre"),
            ("assistant", "Got it."),
        ],
        cached=("other", "repo"),
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_infer_github_repo_scope_uses_cache_when_no_explicit_reference() -> None:
    scope = infer_github_repo_scope(
        message="do these searches",
        conversation_messages=[("user", "hello"), ("assistant", "hi")],
        cached=("Tracer-Cloud", "opensre"),
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_infer_github_repo_scope_uses_github_repository_env() -> None:
    scope = infer_github_repo_scope(
        message="any issues?",
        conversation_messages=[],
        env={"GITHUB_REPOSITORY": "Tracer-Cloud/opensre"},
        cached=None,
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_detect_git_remote_repo_scope_parses_https_remote() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n")
        assert detect_git_remote_repo_scope("/tmp/repo") == ("org", "repo")
    run.assert_called_once()


def test_detect_git_remote_repo_scope_parses_ssh_remote() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="git@github.com:org/repo.git\n")
        assert detect_git_remote_repo_scope() == ("org", "repo")


def test_apply_github_repo_scope_merges_into_github_mcp_config() -> None:
    resolved: dict[str, Any] = {
        "github": GitHubMCPConfig(
            url="https://api.githubcopilot.com/mcp/",
            auth_token="tok",
        )
    }
    merged = apply_github_repo_scope(resolved, "Tracer-Cloud", "opensre")
    gh = merged["github"]
    assert isinstance(gh, dict)
    assert gh["owner"] == "Tracer-Cloud"
    assert gh["repo"] == "opensre"
    assert gh["url"] == "https://api.githubcopilot.com/mcp/"
    assert isinstance(resolved["github"], GitHubMCPConfig)


def test_apply_github_repo_scope_noop_without_github() -> None:
    resolved = {"datadog": {"connection_verified": True}}
    assert apply_github_repo_scope(resolved, "o", "r") == {"datadog": {"connection_verified": True}}
