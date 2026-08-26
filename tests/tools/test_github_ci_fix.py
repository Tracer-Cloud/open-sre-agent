"""Tests for the GitHub PR CI remediation action tool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import AgentToolContext, RegisteredTool
from integrations.coding_agent import CodingResult
from integrations.github.tools.ci_fix.context import (
    CiFixContext,
    FailingCheck,
    gather_branch_ci_fix_context,
    gather_ci_fix_context,
    parse_pr_url,
)
from integrations.github.tools.ci_fix.errors import (
    ERR_INVALID_INPUT,
    ERR_NO_FAILING_CHECKS,
    ERR_PR_NOT_OPEN,
    ERR_UNSUPPORTED_PR_BRANCH,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.runner import run_ci_fix, run_fix, with_push_output
from integrations.github.tools.ci_fix.ship import PushResult, push_ci_fix
from integrations.github.tools.ci_fix.tool import (
    _github_ci_fix_available,
    fix_github_pr_ci,
)
from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification
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
    skipped_check_names=(),
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


_BRANCH_CTX = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=None,
    title="",
    url="https://github.com/Tracer-Cloud/opensre/tree/main",
    base_branch="main",
    head_branch="main",
    head_sha="abc123",
    skipped_check_names=(),
    failing_checks=(
        FailingCheck(
            name="CI",
            conclusion="failure",
            details_url="https://github.com/Tracer-Cloud/opensre/actions/runs/9",
            workflow_name="CI",
            run_id="9",
            log_excerpt="pytest failed",
        ),
    ),
    task="Fix CI on main.",
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
    assert ctx.skipped_check_names == ()
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


def test_push_ci_fix_returns_exact_committed_head_sha() -> None:
    coding_result = CodingResult(success=True, summary="Fix CI.", changed_files=["app.py"])

    with (
        patch("integrations.github.tools.ci_fix.ship.resolve_github_token", return_value="tok"),
        patch("integrations.github.tools.ci_fix.ship.ensure_git_repo"),
        patch(
            "integrations.github.tools.ci_fix.ship.current_branch",
            return_value="feat/fix-ci",
        ),
        patch(
            "integrations.github.tools.ci_fix.ship._changed_since_baseline",
            return_value=["app.py"],
        ),
        patch("integrations.github.tools.ci_fix.ship.commit_paths"),
        patch(
            "integrations.github.tools.ci_fix.ship._head_sha",
            return_value="0123456789abcdef",
        ) as head_sha,
        patch("integrations.github.tools.ci_fix.ship.push_branch"),
    ):
        result = push_ci_fix(
            "/workspace",
            ctx=_CTX,
            result=coding_result,
            github_token="tok",
        )

    assert result.head_sha == "0123456789abcdef"
    head_sha.assert_called_once_with("/workspace")


def test_with_push_output_reports_superseded_commit() -> None:
    output = {
        "owner": "Tracer-Cloud",
        "repo": "opensre",
        "pr_number": 4597,
        "success": True,
    }
    push = PushResult(
        branch_name="feat/fix-ci",
        head_sha="0123456789abcdef",
        changed_files=["app.py"],
    )
    verification = CheckVerification(
        state=CheckState.SUPERSEDED,
        check_names=("quality",),
        observed_head_sha="fedcba9876543210",
    )

    result = with_push_output(output, push, verification)

    assert result["success"] is False
    assert result["checks_state"] == "superseded"
    assert result["error_kind"] == "checks_superseded"
    assert "another commit replaced 0123456789ab" in result["response_text"]


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
    return_value=PushResult(
        branch_name="feat/fix-ci",
        head_sha="new-sha",
        changed_files=["app.py"],
    ),
)
@patch(
    "integrations.github.tools.ci_fix.runner.wait_for_pr_checks",
    return_value=CheckVerification(
        state=CheckState.PASSED,
        check_names=("quality", "test (integrations-and-misc)"),
    ),
)
@patch("integrations.github.tools.ci_fix.runner.run_fix")
@patch("integrations.github.tools.ci_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.ci_fix.runner.checkout_target_branch")
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
    mock_wait: MagicMock,
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
    assert result["checks_state"] == "passed"
    assert result["response_text"] == (
        "Fixed failing CI for Tracer-Cloud/opensre#4597, pushed feat/fix-ci, "
        "and all PR checks passed."
    )
    assert "checking out feat/fix-ci" in prompts[0]
    mock_push.assert_called_once()
    mock_wait.assert_called_once_with(
        _CTX,
        github_token="tok",
        expected_head_sha="new-sha",
    )


@patch(
    "integrations.github.tools.ci_fix.runner.push_ci_fix",
    return_value=PushResult(
        branch_name="feat/fix-ci",
        head_sha="new-sha",
        changed_files=["app.py"],
    ),
)
@patch(
    "integrations.github.tools.ci_fix.runner.wait_for_pr_checks",
    return_value=CheckVerification(
        state=CheckState.FAILED,
        check_names=("quality", "tests"),
        failing_checks=("tests",),
    ),
)
@patch(
    "integrations.github.tools.ci_fix.runner.run_fix",
    return_value=CodingResult(
        success=True,
        summary="Fixed failing pytest expectation.",
        changed_files=["app.py"],
        diff="diff",
    ),
)
@patch("integrations.github.tools.ci_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.ci_fix.runner.checkout_target_branch")
@patch("integrations.github.tools.ci_fix.runner.ensure_push_ready")
@patch("integrations.github.tools.ci_fix.runner.ensure_workspace_ready")
@patch("integrations.github.tools.ci_fix.runner.gather_ci_fix_context", return_value=_CTX)
def test_run_ci_fix_reports_failed_post_push_checks_without_prompting_again(
    _gather: MagicMock,
    _workspace: MagicMock,
    _push_ready: MagicMock,
    _checkout: MagicMock,
    _pre: MagicMock,
    _run_fix: MagicMock,
    _wait: MagicMock,
    _push: MagicMock,
) -> None:
    result = run_ci_fix(
        owner="Tracer-Cloud",
        repo="opensre",
        pr_number=4597,
        github_token="tok",
        confirm_fn=lambda _prompt: "y",
    )

    assert result["success"] is False
    assert result["error_kind"] == "checks_failed"
    assert result["checks_state"] == "failed"
    assert result["response_text"] == (
        "Pushed a CI fix to feat/fix-ci, but PR checks are still failing: tests."
    )
    assert "continue" not in result["response_text"].lower()


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
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(
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


def test_log_excerpt_dedups_overlapping_error_windows() -> None:
    # A dense error-marked region near the end of a log (e.g. a CI gate script
    # echoing ::error lines) used to append one ±window per marker line, so the
    # duplicated tail evicted the actual test failure from the truncated
    # excerpt handed to the coding agent.
    from integrations.github.tools.ci_fix.context import _log_excerpt

    failure = "FAILED tests/x.py::test_real_bug - AssertionError: assert 1 == 2"
    dense_tail = [f"::error gate script line {i} " + "x" * 40 for i in range(40)]
    raw = "\n".join(["setup ok", failure, *dense_tail])

    excerpt = _log_excerpt(raw)

    assert "test_real_bug" in excerpt
    assert excerpt.count("gate script line 39") == 1


def test_gather_ci_fix_context_refuses_merged_pr() -> None:
    # The regression that motivated the guard: a merged Dependabot PR whose
    # head branch was already deleted was accepted and then failed on fetch.
    payload = {**_PR_PAYLOAD, "state": "MERGED"}

    with patch("integrations.github.tools.ci_fix.context.run_gh_json", return_value=payload):
        try:
            gather_ci_fix_context(
                owner="Tracer-Cloud",
                repo="opensre",
                pr_number=4597,
                github_token="tok",
            )
        except GitHubCiFixError as exc:
            assert exc.kind == ERR_PR_NOT_OPEN
            assert "is merged, not open" in exc.message
            assert "no push was made" in exc.message
        else:
            raise AssertionError("expected pr_not_open")


def test_gather_branch_ci_fix_context_builds_task_from_failing_runs() -> None:
    branch_payload = {"sha": "deadbeef1234"}
    runs_payload = {
        "runs": [
            {
                "databaseId": 9,
                "name": "CI",
                "workflowName": "CI",
                "conclusion": "failure",
                "status": "completed",
                "url": "https://github.com/Tracer-Cloud/opensre/actions/runs/9",
            },
            {
                "databaseId": 10,
                "name": "CodeQL",
                "workflowName": "CodeQL",
                "conclusion": "success",
                "status": "completed",
                "url": "https://github.com/Tracer-Cloud/opensre/actions/runs/10",
            },
        ]
    }

    with (
        patch(
            "integrations.github.tools.ci_fix.context.run_gh_json",
            side_effect=[branch_payload, runs_payload],
        ),
        patch(
            "integrations.github.tools.ci_fix.context.run_gh_text",
            return_value="Error: pytest failed",
        ) as log_view,
    ):
        ctx = gather_branch_ci_fix_context(
            branch="main",
            owner="Tracer-Cloud",
            repo="opensre",
            github_token="tok",
        )

    assert ctx.number is None
    assert ctx.is_branch_target is True
    assert ctx.head_branch == "main"
    assert ctx.base_branch == "main"
    assert ctx.head_sha == "deadbeef1234"
    assert [check.name for check in ctx.failing_checks] == ["CI"]
    assert "branch main" in ctx.task
    assert "pytest failed" in ctx.task
    assert log_view.call_args.args[0] == ["run", "view", "9", "--log-failed"]


def test_gather_branch_ci_fix_context_reports_no_failing_runs() -> None:
    with patch(
        "integrations.github.tools.ci_fix.context.run_gh_json",
        side_effect=[{"sha": "deadbeef1234"}, {"runs": []}],
    ):
        try:
            gather_branch_ci_fix_context(
                branch="main",
                owner="Tracer-Cloud",
                repo="opensre",
                github_token="tok",
            )
        except GitHubCiFixError as exc:
            assert exc.kind == ERR_NO_FAILING_CHECKS
            assert "Tracer-Cloud/opensre@main" in exc.message
        else:
            raise AssertionError("expected no_failing_checks")


def test_run_ci_fix_rejects_branch_and_pr_selector_together() -> None:
    result = run_ci_fix(branch="main", pr_number=4597)

    assert result["success"] is False
    assert result["error_kind"] == ERR_INVALID_INPUT
    assert "not both" in result["error"]


@patch(
    "integrations.github.tools.ci_fix.runner.push_ci_fix",
    return_value=PushResult(
        branch_name="main",
        head_sha="new-sha",
        changed_files=["pricing.py"],
    ),
)
@patch(
    "integrations.github.tools.ci_fix.runner.wait_for_branch_checks",
    return_value=CheckVerification(state=CheckState.PASSED, check_names=("CI",)),
)
@patch("integrations.github.tools.ci_fix.runner.wait_for_pr_checks")
@patch(
    "integrations.github.tools.ci_fix.runner.run_fix",
    return_value=CodingResult(
        success=True,
        summary="Aligned pricing fallback.",
        changed_files=["pricing.py"],
        diff="diff",
    ),
)
@patch("integrations.github.tools.ci_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.ci_fix.runner.checkout_target_branch")
@patch("integrations.github.tools.ci_fix.runner.ensure_push_ready")
@patch("integrations.github.tools.ci_fix.runner.ensure_workspace_ready")
@patch(
    "integrations.github.tools.ci_fix.runner.gather_branch_ci_fix_context",
    return_value=_BRANCH_CTX,
)
def test_run_ci_fix_branch_mode_pushes_directly_to_branch(
    _gather: MagicMock,
    _workspace: MagicMock,
    _push_ready: MagicMock,
    _checkout: MagicMock,
    _pre: MagicMock,
    _run_fix: MagicMock,
    mock_pr_wait: MagicMock,
    mock_branch_wait: MagicMock,
    mock_push: MagicMock,
) -> None:
    prompts: list[str] = []

    result = run_ci_fix(
        branch="main",
        github_token="tok",
        confirm_fn=lambda prompt: prompts.append(prompt) or "y",
    )

    assert result["success"] is True
    assert result["pr_number"] is None
    assert result["pr_url"] == ""
    assert result["branch_name"] == "main"
    assert result["checks_state"] == "passed"
    assert result["response_text"] == (
        "Fixed failing CI for Tracer-Cloud/opensre@main, pushed main, "
        "and all the checks on main passed."
    )
    assert "pushing directly to main" in prompts[0]
    mock_push.assert_called_once()
    mock_branch_wait.assert_called_once_with(
        _BRANCH_CTX,
        github_token="tok",
        expected_head_sha="new-sha",
    )
    mock_pr_wait.assert_not_called()


def test_push_ci_fix_branch_mode_pushes_protected_branch_with_opt_in() -> None:
    coding_result = CodingResult(success=True, summary="Fix CI.", changed_files=["pricing.py"])

    with (
        patch("integrations.github.tools.ci_fix.ship.resolve_github_token", return_value="tok"),
        patch("integrations.github.tools.ci_fix.ship.ensure_git_repo"),
        patch("integrations.github.tools.ci_fix.ship.current_branch", return_value="main"),
        patch(
            "integrations.github.tools.ci_fix.ship._changed_since_baseline",
            return_value=["pricing.py"],
        ),
        patch("integrations.github.tools.ci_fix.ship.commit_paths") as commit,
        patch("integrations.github.tools.ci_fix.ship._head_sha", return_value="new-sha"),
        patch("integrations.github.tools.ci_fix.ship.push_branch") as push,
    ):
        result = push_ci_fix(
            "/workspace",
            ctx=_BRANCH_CTX,
            result=coding_result,
            github_token="tok",
        )

    assert result.branch_name == "main"
    assert push.call_args.kwargs["allow_protected"] is True
    assert push.call_args.kwargs["base_default"] == ""
    assert commit.call_args.args[2].splitlines()[0].startswith("fix: repair CI for main - ")


def test_skill_guidance_attaches_to_ci_fix_tool() -> None:
    clear_tool_registry_cache()
    tools_by_name = {tool.name: tool for tool in get_registered_tools()}
    tool = tools_by_name["fix_github_pr_ci"]

    assert "Workflow guidance:" in tool.description
    assert '<skill name="github-ci-fix"' in tool.skill_guidance
    assert "Fork PR branches are refused" in tool.skill_guidance
    assert "post-push check verification" in tool.skill_guidance
