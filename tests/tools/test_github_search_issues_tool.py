"""Tests for GitHubSearchIssuesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GitHubSearchIssuesTool import (
    build_github_issue_search_query,
    search_github_issues,
)
from tests.tools.conftest import BaseToolContract


class TestGitHubSearchIssuesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_github_issues.__opensre_registered_tool__


def test_metadata() -> None:
    rt = search_github_issues.__opensre_registered_tool__
    assert rt.name == "search_github_issues"
    assert rt.source == "github"


def test_is_available() -> None:
    rt = search_github_issues.__opensre_registered_tool__
    assert (
        rt.is_available({"github": {"owner": "o", "repo": "r", "connection_verified": True}})
        is True
    )
    assert rt.is_available({"github": {"owner": "o", "repo": "r"}}) is False
    assert rt.is_available({}) is False


def test_extract_params() -> None:
    rt = search_github_issues.__opensre_registered_tool__
    sources = {
        "github": {
            "owner": "test-owner",
            "repo": "test-repo",
            "github_token": "token123",
            "connection_verified": True,
        }
    }
    params = rt.extract_params(sources)
    assert params["owner"] == "test-owner"
    assert params["repo"] == "test-repo"
    assert params["query"] == "bug"
    assert params["github_token"] == "token123"


def test_build_github_issue_search_query() -> None:
    q = build_github_issue_search_query("owner", "repo", "test error")
    assert q == "test error repo:owner/repo"

    q_already_qualified = build_github_issue_search_query("owner", "repo", "error repo:owner/repo")
    assert q_already_qualified == "error repo:owner/repo"


def test_run_happy_path() -> None:
    fake_config = MagicMock()
    fake_mcp_res = {
        "is_error": False,
        "tool": "search_issues",
        "structured_content": [{"number": 42, "title": "Memory leak in handler", "state": "open"}],
    }

    with (
        patch(
            "app.tools.GitHubSearchIssuesTool.resolve_github_mcp_config", return_value=fake_config
        ),
        patch("app.tools.GitHubSearchIssuesTool.call_github_mcp_tool", return_value=fake_mcp_res),
    ):
        result = search_github_issues(
            owner="owner",
            repo="repo",
            query="memory leak",
        )

    assert result["available"] is True
    assert len(result["issues"]) == 1
    assert result["issues"][0]["number"] == 42
    assert result["query"] == "memory leak repo:owner/repo"


def test_run_no_mcp_config() -> None:
    with patch("app.tools.GitHubSearchIssuesTool.resolve_github_mcp_config", return_value=None):
        result = search_github_issues(
            owner="owner",
            repo="repo",
            query="bug",
        )

    assert result["available"] is False
    assert result["issues"] == []
