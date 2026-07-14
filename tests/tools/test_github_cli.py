"""Tests for coworker-style authenticated GitHub CLI tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    execute_tool_calls,
)
from core.llm.types import ToolCall
from core.tool_framework.registered_tool import RegisteredTool
from tests.tools.conftest import BaseToolContract
from tools.github_cli.classify import classify_gh_args
from tools.github_cli.runner import build_gh_argv, run_gh
from tools.github_cli.tool import github_cli, github_cli_write
from tools.registry import clear_tool_registry_cache, get_registered_tools


def _registered(tool: Any) -> RegisteredTool:
    return tool.__opensre_registered_tool__


class TestGithubCliContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(github_cli)


class TestGithubCliWriteContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(github_cli_write)


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


def test_github_cli_rejects_mutate() -> None:
    result = github_cli(args=["issue", "create", "--title", "x"])
    assert result["ok"] is False
    assert result["error_type"] == "wrong_tool"
    assert result["suggested_tool"] == "github_cli_write"


def test_github_cli_write_rejects_read() -> None:
    result = github_cli_write(args=["issue", "list"])
    assert result["ok"] is False
    assert result["error_type"] == "wrong_tool"
    assert result["suggested_tool"] == "github_cli"


def test_github_cli_write_metadata_requires_approval() -> None:
    tool = _registered(github_cli_write)
    assert tool.requires_approval is True
    assert "mutating" in tool.approval_reason.lower() or "gh" in tool.approval_reason.lower()
    assert tool.surfaces == ("chat", "action")
    assert "investigation" not in tool.surfaces


def test_github_cli_read_metadata() -> None:
    tool = _registered(github_cli)
    assert tool.requires_approval is False
    assert tool.side_effect_level == "read_only"
    assert "action" in tool.surfaces


def test_github_cli_write_create_issue_mocked() -> None:
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
        result = github_cli_write(
            args=["issue", "create", "--title", "t", "--body", "b"],
            repo="o/r",
        )
    assert result["ok"] is True
    assert "issues/99" in result["stdout"]
    assert result["tool"] == "github_cli_write"
    run_mock.assert_called_once()


def test_github_cli_write_approval_hook() -> None:
    tool = _registered(github_cli_write)

    def approve(request: ToolExecutionRequest) -> BeforeToolCallResult:
        assert request.tool.requires_approval is True
        return BeforeToolCallResult(approved=True)

    with patch(
        "tools.github_cli.tool.run_gh",
        return_value={
            "ok": True,
            "argv": ["gh", "issue", "create", "--title", "t"],
            "exit_code": 0,
            "stdout": "https://github.com/o/r/issues/1\n",
            "stderr": "",
        },
    ):
        result = execute_tool_calls(
            [
                ToolCall(
                    id="c1",
                    name="github_cli_write",
                    input={"args": ["issue", "create", "--title", "t"]},
                )
            ],
            [tool],
            {},
            hooks=ToolExecutionHooks(before_tool_call=approve),
        )[0]

    assert result.is_error is False


def test_skill_guidance_attaches_to_github_cli_tools() -> None:
    clear_tool_registry_cache()
    tools_by_name = {t.name: t for t in get_registered_tools()}
    for name in ("github_cli", "github_cli_write"):
        tool = tools_by_name[name]
        assert "Workflow guidance:" in tool.description
        assert "github_cli" in tool.skill_guidance
        assert "shell_run" in tool.skill_guidance.lower() or "Never" in tool.skill_guidance
