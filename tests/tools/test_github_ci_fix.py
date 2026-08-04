"""Tests for the GitHub PR CI remediation action tool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolContext,
)
from core.tool_framework.registered_tool import RegisteredTool
from core.types import AgentToolContext
from integrations.coding_agent import CodingResult
from integrations.github.tools.ci_fix.context import (
    CiFixContext,
    FailingCheck,
    gather_ci_fix_context,
    parse_pr_url,
)
from integrations.github.tools.ci_fix.errors import (
    ERR_NO_FAILING_CHECKS,
    ERR_UNSUPPORTED_PR_BRANCH,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.runner import run_ci_fix, run_fix
from integrations.github.tools.ci_fix.ship import PushResult
from integrations.github.tools.ci_fix.tool import (
    _github_ci_fix_available,
    fix_github_pr_ci,
)
from tests.tools.conftest import BaseToolContract
from tools.registry import clear_tool_registry_cache, get_registered_tool_map, get_registered_tools

_PR_PAYLOAD: dict[str, Any] = {
    "number": 4597,
    "title": "feat: add fixer",
    "url": "https://github.com/Tracer-Cloud/opensre/pull/4597",
    "headRefName": "feat/fix-ci",
    "headRefOid": "abc123",
    "headRepositoryOwner": {"login": "Tracer-Cloud"},
    "headRepository": {"name": "opensre", "nameWithOwner": "Tracer-Cloud/opensre"},
    "baseRefName": "main",
    "isCrossRepository": False,
    "state": "OPEN",
    "statusCheckRollup": [
        {
            "__typename": "CheckRun",
            "name": "test (integrations-and-misc)",
            "conclusion": "FAILURE",
            "detailsUrl": "https://github.com/Tracer-Cloud/opensre/actions/runs/1/job/2",
            "workflowName": "CI",
        },
        {
            "__typename": "CheckRun",
            "name": "quality",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://github.com/Tracer-Cloud/opensre/actions/runs/1/job/3",
            "workflowName": "CI",
        },
    ],
}

_CTX = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=4597,
    title="feat: add fixer",
    url="https://github.com/Tracer-Cloud/opensre/pull/4597",
    base_branch="main",
    head_branch="feat/fix-ci",
    head_sha="abc123",
    failing_checks=(
        FailingCheck(
            name="test (integrations-and-misc)",
            conclusion="failure",
            details_url="https://github.com/Tracer-Cloud/opensre/actions/runs/1/job/2",
            workflow_name="CI",
            run_id="1",
            job_id="2",
            log_excerpt="pytest failed",
        ),
    ),
    task="Fix CI.",
)


def _registered(tool: Any) -> RegisteredTool:
    return tool.__opensre_registered_tool__


class TestGitHubCiFixContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(fix_github_pr_ci)


def test_parse_pr_url_supports_github_pull_request_urls() -> None:
    parsed = parse_pr_url("https://github.com/Tracer-Cloud/opensre/pull/4597")

    assert parsed is not None
    assert parsed.owner == "Tracer-Cloud"
    assert parsed.repo == "opensre"
    assert parsed.number == 4597


def test_available_when_github_token_present(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _github_ci_fix_available({}) is False

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert _github_ci_fix_available({}) is True


def test_gather_ci_fix_context_builds_task_with_failing_logs() -> None:
    with (
        patch(
            "integrations.github.tools.ci_fix.context.run_gh_json",
            return_value=_PR_PAYLOAD,
        ) as pr_view,
        patch(
            "integrations.github.tools.ci_fix.context.run_gh_text",
            return_value="setup ok\nError: pytest failed\nsee report",
        ) as log_view,
    ):
        ctx = gather_ci_fix_context(
            owner="Tracer-Cloud",
            repo="opensre",
            pr_number=4597,
            github_token="tok",
        )

    assert ctx.number == 4597
    assert ctx.head_branch == "feat/fix-ci"
    assert ctx.failing_checks[0].name == "test (integrations-and-misc)"
    assert "pytest failed" in ctx.task
    assert "Head branch to edit and push: feat/fix-ci" in ctx.task
    pr_view.assert_called_once()
    log_view.assert_called_once()
    assert log_view.call_args.args[0] == ["run", "view", "1", "--log", "--job", "2"]


def test_gather_ci_fix_context_reports_no_failing_checks() -> None:
    payload = {**_PR_PAYLOAD, "statusCheckRollup": []}

    with patch("integrations.github.tools.ci_fix.context.run_gh_json", return_value=payload):
        try:
            gather_ci_fix_context(
                owner="Tracer-Cloud",
                repo="opensre",
                pr_number=4597,
                github_token="tok",
            )
        except GitHubCiFixError as exc:
            assert exc.kind == ERR_NO_FAILING_CHECKS
            assert exc.message == (
                "No failing CI checks found on Tracer-Cloud/opensre#4597; no push was made."
            )
        else:
            raise AssertionError("expected no_failing_checks")


def test_gather_ci_fix_context_ignores_cancelled_sibling_checks() -> None:
    payload = {
        **_PR_PAYLOAD,
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "coverage-report",
                "conclusion": "CANCELLED",
                "detailsUrl": "https://github.com/Tracer-Cloud/opensre/actions/runs/1/job/9",
                "workflowName": "CI",
            },
            {
                "__typename": "CheckRun",
                "name": "quality",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/Tracer-Cloud/opensre/actions/runs/1/job/3",
                "workflowName": "CI",
            },
        ],
    }

    with (
        patch("integrations.github.tools.ci_fix.context.run_gh_json", return_value=payload),
        patch(
            "integrations.github.tools.ci_fix.context.run_gh_text",
            return_value="Error: ruff failed",
        ),
    ):
        ctx = gather_ci_fix_context(
            owner="Tracer-Cloud",
            repo="opensre",
            pr_number=4597,
            github_token="tok",
        )

    assert [check.name for check in ctx.failing_checks] == ["quality"]


def test_commit_message_skips_markdown_summary_heading() -> None:
    from integrations.github.tools.ci_fix.ship import _commit_message

    message = _commit_message(
        _CTX,
        "## Summary\n\n**Root cause:** FakePopen lacked pid.\n",
    )
    subject = message.splitlines()[0]
    assert "## Summary" not in subject
    assert "FakePopen lacked pid" in subject


def test_gather_ci_fix_context_refuses_fork_branch() -> None:
    payload = {
        **_PR_PAYLOAD,
        "isCrossRepository": True,
        "headRepositoryOwner": {"login": "someone"},
        "headRepository": {"name": "opensre", "nameWithOwner": "someone/opensre"},
    }

    with patch("integrations.github.tools.ci_fix.context.run_gh_json", return_value=payload):
        try:
            gather_ci_fix_context(
                owner="Tracer-Cloud",
                repo="opensre",
                pr_number=4597,
                github_token="tok",
            )
        except GitHubCiFixError as exc:
            assert exc.kind == ERR_UNSUPPORTED_PR_BRANCH
            assert "only pushes CI fixes to branches in the same repository" in exc.message
            assert "\n" not in exc.message
        else:
            raise AssertionError("expected unsupported_pr_branch")


def test_run_fix_without_coding_agent_is_backend_neutral() -> None:
    with patch(
        "integrations.github.tools.ci_fix.runner.verify_coding_agent",
        return_value=(False, "pi missing; codex missing"),
    ):
        result = run_fix(_CTX, "/workspace", model=None)

    assert result.success is False
    assert result.error == (
        "Found failing CI checks on Tracer-Cloud/opensre#4597, "
        "but no configured coding agent is ready; no push was made."
    )
    assert "pi missing" not in result.error


@patch(
    "integrations.github.tools.ci_fix.runner.push_ci_fix",
    return_value=PushResult(branch_name="feat/fix-ci", changed_files=["app.py"]),
)
@patch("integrations.github.tools.ci_fix.runner.run_fix")
@patch("integrations.github.tools.ci_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.ci_fix.runner.checkout_pr_branch")
@patch("integrations.github.tools.ci_fix.runner.ensure_push_ready")
@patch("integrations.github.tools.ci_fix.runner.ensure_workspace_ready")
@patch("integrations.github.tools.ci_fix.runner.gather_ci_fix_context", return_value=_CTX)
def test_run_ci_fix_success_pushes_existing_pr_branch(
    _gather: MagicMock,
    _workspace: MagicMock,
    _push_ready: MagicMock,
    _checkout: MagicMock,
    _pre: MagicMock,
    mock_run_fix: MagicMock,
    mock_push: MagicMock,
) -> None:
    prompts: list[str] = []
    mock_run_fix.return_value = CodingResult(
        success=True,
        summary="Fixed failing pytest expectation.",
        changed_files=["app.py"],
        diff="diff",
    )

    result = run_ci_fix(
        owner="Tracer-Cloud",
        repo="opensre",
        pr_number=4597,
        github_token="tok",
        confirm_fn=lambda prompt: prompts.append(prompt) or "y",
    )

    assert result["success"] is True
    assert result["branch_name"] == "feat/fix-ci"
    assert result["changed_files"] == ["app.py"]
    assert result["response_text"] == (
        "Fixed failing CI for Tracer-Cloud/opensre#4597 and pushed feat/fix-ci."
    )
    assert "checking out feat/fix-ci" in prompts[0]
    mock_push.assert_called_once()


def test_run_ci_fix_no_failing_checks_response_text() -> None:
    with patch(
        "integrations.github.tools.ci_fix.runner.gather_ci_fix_context",
        side_effect=GitHubCiFixError(
            ERR_NO_FAILING_CHECKS,
            "No failing CI checks found on Tracer-Cloud/opensre#4597; no push was made.",
        ),
    ):
        result = run_ci_fix(owner="Tracer-Cloud", repo="opensre", pr_number=4597)

    assert result["success"] is False
    assert result["error_kind"] == ERR_NO_FAILING_CHECKS
    assert result["response_text"] == result["error"]
    assert "\n" not in result["response_text"]


def test_tool_passes_shell_confirmation_function() -> None:
    def confirm(_prompt: str) -> str:
        return "y"

    agent_context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolContext(
                session=object(),
                console=SimpleNamespace(),
                confirm_fn=confirm,
            )
        },
    )

    with patch(
        "integrations.github.tools.ci_fix.tool.run_ci_fix", return_value={"success": True}
    ) as runner:
        result = fix_github_pr_ci(context=agent_context)

    assert result == {"success": True}
    assert runner.call_args.kwargs["confirm_fn"] is confirm


def test_registry_discovers_ci_fix_on_action_surface() -> None:
    clear_tool_registry_cache()
    action = get_registered_tool_map("action")
    investigation = get_registered_tool_map("investigation")
    chat = get_registered_tool_map("chat")

    tool = action["fix_github_pr_ci"]
    assert tool.surfaces == ("action",)
    assert tool.requires_approval is True
    assert tool.side_effect_level == "mutating"
    assert "fix_github_pr_ci" not in investigation
    assert "fix_github_pr_ci" not in chat


def test_skill_guidance_attaches_to_ci_fix_tool() -> None:
    clear_tool_registry_cache()
    tools_by_name = {tool.name: tool for tool in get_registered_tools()}
    tool = tools_by_name["fix_github_pr_ci"]

    assert "Workflow guidance:" in tool.description
    assert '<skill name="github-ci-fix"' in tool.skill_guidance
    assert "Fork PR branches are refused" in tool.skill_guidance
