"""Tests for coworker-style authenticated GitHub CLI tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.tool_framework.registered_tool import RegisteredTool
from tests.tools.conftest import BaseToolContract
from tools.github_cli.classify import classify_gh_args
from tools.github_cli.runner import build_gh_argv, run_gh
from tools.github_cli.tool import github_cli
from tools.registry import clear_tool_registry_cache, get_registered_tools


def _registered(tool: Any) -> RegisteredTool:
    return tool.__opensre_registered_tool__


class TestGithubCliContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(github_cli)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["issue", "list"], "read"),
        (["issue", "view", "12"], "read"),
        (["-R", "o/r", "issue", "list"], "read"),
        (["pr", "view", "3"], "read"),
        (["repo", "view"], "read"),
        (["api", "repos/o/r"], "read"),
        (["api", "-X", "GET", "repos/o/r"], "read"),
        (["search", "issues", "crash"], "read"),
        (["auth", "status"], "read"),
        (["status"], "read"),
        (["issue", "create", "--title", "t"], "mutate"),
        (["issue", "close", "1"], "mutate"),
        (["pr", "merge", "2"], "mutate"),
        (["api", "-X", "POST", "repos/o/r/issues"], "mutate"),
        (["api", "-F", "title=hi", "repos/o/r/issues"], "mutate"),
        (["label", "create", "bug"], "mutate"),
        ([], "mutate"),
        (["unknown", "thing"], "mutate"),
    ],
)
def test_classify_gh_args(args: list[str], expected: str) -> None:
    assert classify_gh_args(args) == expected


def test_build_gh_argv_includes_repo_flag() -> None:
    assert build_gh_argv(args=["issue", "list"], repo="acme/widgets") == [
        "gh",
        "-R",
        "acme/widgets",
        "issue",
        "list",
    ]


def test_build_gh_argv_skips_repo_flag_for_api() -> None:
    """``gh api`` rejects ``-R``; repo belongs in the API path."""
    assert build_gh_argv(
        args=["api", "repos/acme/widgets/pulls/1/comments"],
        repo="acme/widgets",
    ) == ["gh", "api", "repos/acme/widgets/pulls/1/comments"]


def test_build_gh_argv_skips_repo_flag_for_api_after_global_flags() -> None:
    assert build_gh_argv(
        args=["--hostname", "github.com", "api", "user"],
        repo="acme/widgets",
    ) == ["gh", "--hostname", "github.com", "api", "user"]


def test_run_gh_missing_token() -> None:
    with patch("tools.github_cli.runner.resolve_github_token", return_value=""):
        result = run_gh(args=["issue", "list"])
    assert result["ok"] is False
    assert result["error_type"] == "configuration_error"


def test_run_gh_missing_binary() -> None:
    with (
        patch("tools.github_cli.runner.resolve_github_token", return_value="tok"),
        patch("tools.github_cli.runner.shutil.which", return_value=None),
    ):
        result = run_gh(args=["issue", "list"])
    assert result["ok"] is False
    assert result["error_type"] == "missing_binary"


def test_run_gh_injects_token_env() -> None:
    completed = MagicMock(returncode=0, stdout="https://github.com/o/r/issues/1\n", stderr="")
    with (
        patch("tools.github_cli.runner.resolve_github_token", return_value="secret-token"),
        patch("tools.github_cli.runner.shutil.which", return_value="/usr/bin/gh"),
        patch("tools.github_cli.runner.subprocess.run", return_value=completed) as run_mock,
    ):
        result = run_gh(args=["issue", "create", "--title", "t"], repo="o/r")

    assert result["ok"] is True
    assert "secret-token" not in str(result)
    env = run_mock.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == "secret-token"
    assert env["GITHUB_TOKEN"] == "secret-token"
    assert run_mock.call_args.args[0] == [
        "gh",
        "-R",
        "o/r",
        "issue",
        "create",
        "--title",
        "t",
    ]


def test_github_cli_runs_mutate_without_approval() -> None:
    tool = _registered(github_cli)
    assert tool.requires_approval is False
    assert "action" in tool.surfaces

    with patch(
        "tools.github_cli.tool.run_gh",
        return_value={
            "ok": True,
            "argv": ["gh", "issue", "create", "--title", "t"],
            "exit_code": 0,
            "stdout": "https://github.com/o/r/issues/99\n",
            "stderr": "",
        },
    ) as run_mock:
        result = github_cli(
            args=["issue", "create", "--title", "t", "--body", "b"],
            repo="o/r",
        )
    assert result["ok"] is True
    assert result["effect"] == "mutate"
    assert result["tool"] == "github_cli"
    assert "issues/99" in result["stdout"]
    run_mock.assert_called_once()


def test_github_cli_runs_read() -> None:
    with patch(
        "tools.github_cli.tool.run_gh",
        return_value={
            "ok": True,
            "argv": ["gh", "issue", "list"],
            "exit_code": 0,
            "stdout": "1\tOpen bug\n",
            "stderr": "",
        },
    ):
        result = github_cli(args=["issue", "list"], repo="o/r")
    assert result["ok"] is True
    assert result["effect"] == "read"


def test_skill_guidance_attaches_to_github_cli() -> None:
    clear_tool_registry_cache()
    tools_by_name = {t.name: t for t in get_registered_tools()}
    assert "github_cli_write" not in tools_by_name
    tool = tools_by_name["github_cli"]
    assert "Workflow guidance:" in tool.description
    assert "github_cli" in tool.skill_guidance
    assert "shell_run" in tool.skill_guidance.lower() or "Never" in tool.skill_guidance
    # Capability map + failure clause must survive the registry truncation budget.
    assert "Create issue" in tool.skill_guidance
    assert "Arbitrary API" in tool.skill_guidance
    assert "failed to run" in tool.skill_guidance.lower()
    assert not tool.skill_guidance.endswith("...")
