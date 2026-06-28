"""Tests for semantic GitHub workflow tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    execute_tool_calls,
)
from core.llm.types import ToolCall
from tests.tools.conftest import BaseToolContract
from tools.github.work_status import (
    execute_github_issue_mutation,
    list_github_security_alerts,
    list_github_work_items,
    propose_github_issue_mutation_from_slack,
    summarize_github_pr_status,
)
from tools.github.workflow import GitHubApiError, GitHubRestClient, build_work_status_report
from tools.registered_tool import RegisteredTool
from tools.work_status_report_tool import generate_work_status_report


def _registered_tool(tool: Any) -> Any:
    return tool.__opensre_registered_tool__


class TestListGitHubWorkItemsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(list_github_work_items)


class TestSummarizeGitHubPrStatusContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(summarize_github_pr_status)


class TestListGitHubSecurityAlertsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(list_github_security_alerts)


class TestProposeGitHubIssueMutationFromSlackContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(propose_github_issue_mutation_from_slack)


class TestExecuteGitHubIssueMutationContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(execute_github_issue_mutation)


def test_list_github_work_items_classifies_taken_and_up_for_grabs() -> None:
    issues = [
        {
            "number": 1,
            "title": "Assigned bug",
            "state": "open",
            "html_url": "https://github.com/o/r/issues/1",
            "user": {"login": "alice"},
            "assignees": [{"login": "bob"}],
            "labels": [{"name": "bug"}],
            "updated_at": "2026-06-28T10:00:00Z",
        },
        {
            "number": 2,
            "title": "Starter task",
            "state": "open",
            "html_url": "https://github.com/o/r/issues/2",
            "user": {"login": "carol"},
            "assignees": [],
            "labels": [{"name": "help wanted"}],
            "updated_at": "2026-06-28T11:00:00Z",
        },
        {"number": 3, "pull_request": {}, "title": "PR returned from issues endpoint"},
    ]
    with patch.object(GitHubRestClient, "paginate", return_value=issues):
        result = list_github_work_items(owner="o", repo="r", github_token="tok")

    assert result["available"] is True
    assert result["counts"] == {"total": 2, "taken": 1, "up_for_grabs": 1, "unassigned": 0}
    assert [item["work_status"] for item in result["items"]] == ["taken", "up_for_grabs"]


def test_summarize_github_pr_status_uses_detail_mergeability_not_list_nulls() -> None:
    list_pr = {
        "number": 10,
        "title": "Ready PR",
        "draft": False,
        "html_url": "https://github.com/o/r/pull/10",
        "user": {"login": "alice"},
        "head": {"sha": "abc", "ref": "feature"},
        "mergeable": None,
        "mergeable_state": "unknown",
        "updated_at": "2026-06-28T10:00:00Z",
    }
    detail_pr = {**list_pr, "mergeable": True, "mergeable_state": "clean"}

    def fake_request(self: GitHubRestClient, method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/repos/o/r/pulls/10":
            return detail_pr
        if path == "/repos/o/r/commits/abc/check-runs":
            return {
                "check_runs": [{"name": "test", "conclusion": "success", "status": "completed"}]
            }
        raise AssertionError((method, path))

    with (
        patch.object(GitHubRestClient, "paginate", return_value=[list_pr]),
        patch.object(GitHubRestClient, "request", fake_request),
    ):
        result = summarize_github_pr_status(owner="o", repo="r", github_token="tok")

    assert result["counts"]["mergeable"] == 1
    assert result["pull_requests"][0]["mergeability"] == "mergeable"


def test_summarize_github_pr_status_reports_unknown_mergeability() -> None:
    pr = {
        "number": 11,
        "title": "Unknown PR",
        "draft": False,
        "html_url": "https://github.com/o/r/pull/11",
        "user": {"login": "bob"},
        "head": {"sha": "def", "ref": "bugfix"},
        "mergeable": None,
        "mergeable_state": "unknown",
        "updated_at": "2026-06-28T11:00:00Z",
    }

    def fake_request(self: GitHubRestClient, method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/repos/o/r/pulls/11":
            return pr
        if path == "/repos/o/r/commits/def/check-runs":
            return {"check_runs": []}
        raise AssertionError((method, path))

    with (
        patch.object(GitHubRestClient, "paginate", return_value=[pr]),
        patch.object(GitHubRestClient, "request", fake_request),
    ):
        result = summarize_github_pr_status(owner="o", repo="r", github_token="tok")

    assert result["counts"]["unknown"] == 1
    assert result["pull_requests"][0]["status"] == "unknown"
    assert "mergeability unknown" in result["pull_requests"][0]["blocking_reasons"]


def test_generate_work_status_report_surfaces_fetch_errors() -> None:
    with (
        patch(
            "tools.work_status_report_tool.list_github_work_items",
            return_value={"available": False, "error": "boom", "items": []},
        ),
        patch(
            "tools.work_status_report_tool.summarize_github_pr_status",
            return_value={"available": True, "pull_requests": []},
        ),
    ):
        result = generate_work_status_report(owner="o", repo="r", github_token="tok")

    assert result["available"] is False
    assert result["incomplete"] is True
    assert result["errors"] == ["work_items: boom"]


def test_build_work_status_report_does_not_hide_read_errors() -> None:
    report = build_work_status_report(
        work_items=[],
        pull_requests=[],
        context="today",
        errors=["pull_requests: failed"],
    )

    assert report.available is False
    assert report.incomplete is True
    assert "Incomplete report" in report.slack_markdown


def test_summarize_community_followups_uses_repository_comments_endpoint() -> None:
    with patch.object(
        GitHubRestClient,
        "paginate",
        return_value=[
            {
                "issue_url": "https://api.github.com/repos/o/r/issues/7",
                "body": "When is the meeting?",
                "user": {"login": "contributor"},
                "created_at": "2026-06-28T10:00:00Z",
                "html_url": "u",
            }
        ],
    ) as paginate:
        result = __import__(
            "tools.community_followup_tool", fromlist=["summarize_community_followups"]
        ).summarize_community_followups(owner="o", repo="r", github_token="tok")

    paginate.assert_called_once()
    assert paginate.call_args.args[0] == "/repos/o/r/issues/comments"
    assert result["counts"]["unanswered_questions"] == 1


def test_proposal_id_is_stable_and_payload_has_idempotency_marker() -> None:
    first = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="create",
        slack_text="add this to the hackathon list",
        slack_url="https://slack.example/archives/C/p1",
        labels=["hackathon"],
    )
    second = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="create",
        slack_text="add this to the hackathon list",
        slack_url="https://slack.example/archives/C/p1",
        labels=["hackathon"],
    )

    assert first["proposal"]["proposal_id"] == second["proposal"]["proposal_id"]
    assert first["proposal"]["operation"] == "create"
    assert "opensre-slack-proposal:" in first["proposal"]["payload"]["body"]
    assert first["side_effects"] == []


def test_execute_tool_schema_has_no_confirm_and_requires_approval_metadata() -> None:
    tool = _registered_tool(execute_github_issue_mutation)
    assert "confirm" not in tool.input_schema["properties"]
    assert tool.requires_approval is True
    assert tool.approval_scope == "one_shot"
    assert "GitHub issue" in tool.approval_reason


def test_execute_create_searches_idempotency_marker_before_create() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="create",
        slack_text="ship this",
        slack_url="https://slack.example/archives/C/p1",
    )["proposal"]
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(self: GitHubRestClient, method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        if path == "/search/issues":
            return {"total_count": 0, "items": []}
        if path == "/repos/o/r/issues":
            return {"number": 99, "html_url": "https://github.com/o/r/issues/99"}
        raise AssertionError((method, path))

    with patch.object(GitHubRestClient, "request", fake_request):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["executed"] is True
    assert result["side_effect"] == "created_github_issue"
    assert [call[1] for call in calls] == ["/search/issues", "/repos/o/r/issues"]
    assert "in:body" in calls[0][2]["params"]["q"]
    assert proposal["idempotency_marker"] in calls[0][2]["params"]["q"]


def test_execute_create_returns_existing_issue_for_idempotency_marker() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="create",
        slack_text="ship this",
        slack_url="https://slack.example/archives/C/p1",
    )["proposal"]

    def fake_request(self: GitHubRestClient, method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/search/issues":
            return {"total_count": 1, "items": [{"number": 12, "html_url": "existing"}]}
        raise AssertionError((method, path))

    with patch.object(GitHubRestClient, "request", fake_request):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["executed"] is False
    assert result["side_effect"] == "existing_github_issue"


def test_execute_update_adds_comment_and_preserves_body() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="update",
        issue_number=51,
        slack_text="PR shipped",
        slack_url="https://slack.example/archives/C/p2",
        labels=["done"],
    )["proposal"]
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(self: GitHubRestClient, method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        if path == "/repos/o/r/issues/51" and method == "GET":
            return {"number": 51, "body": "original"}
        if path == "/search/issues":
            assert "in:comments" in kwargs["params"]["q"]
            return {"total_count": 0, "items": []}
        if path == "/repos/o/r/issues/51/comments":
            assert "PR shipped" in kwargs["body"]["body"]
            return {"id": 1}
        if path == "/repos/o/r/issues/51" and method == "PATCH":
            assert "body" not in kwargs["body"]
            assert kwargs["body"] == {"labels": ["done"]}
            return {"number": 51}
        raise AssertionError((method, path, kwargs))

    with patch.object(GitHubRestClient, "request", fake_request):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["executed"] is True
    assert [call[1] for call in calls] == [
        "/repos/o/r/issues/51",
        "/search/issues",
        "/repos/o/r/issues/51/comments",
        "/repos/o/r/issues/51",
    ]


def test_execute_close_comments_before_closing_and_preserves_body() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="close",
        issue_number=51,
        slack_text="done",
        slack_url="https://slack.example/archives/C/p3",
    )["proposal"]
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(self: GitHubRestClient, method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        if path == "/repos/o/r/issues/51" and method == "GET":
            return {"number": 51, "body": "original"}
        if path == "/search/issues":
            assert "in:comments" in kwargs["params"]["q"]
            return {"total_count": 0, "items": []}
        if path == "/repos/o/r/issues/51/comments":
            return {"id": 1}
        if path == "/repos/o/r/issues/51" and method == "PATCH":
            assert kwargs["body"] == {"state": "closed", "state_reason": "completed"}
            return {"number": 51, "state": "closed"}
        raise AssertionError((method, path, kwargs))

    with patch.object(GitHubRestClient, "request", fake_request):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["executed"] is True
    assert [call[1] for call in calls] == [
        "/repos/o/r/issues/51",
        "/search/issues",
        "/repos/o/r/issues/51/comments",
        "/repos/o/r/issues/51",
    ]


def test_execute_update_skips_duplicate_comment_for_seen_marker() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="update",
        issue_number=51,
        slack_text="PR shipped",
        slack_url="https://slack.example/archives/C/p2",
        labels=["done"],
    )["proposal"]
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(self: GitHubRestClient, method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        if path == "/repos/o/r/issues/51" and method == "GET":
            return {"number": 51, "body": "original"}
        if path == "/search/issues":
            return {"total_count": 1, "items": [{"number": 51}]}
        if path == "/repos/o/r/issues/51/comments":
            raise AssertionError("duplicate comment should not be posted")
        if path == "/repos/o/r/issues/51" and method == "PATCH":
            return {"number": 51}
        raise AssertionError((method, path, kwargs))

    with patch.object(GitHubRestClient, "request", fake_request):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["executed"] is True
    assert result["comment_already_recorded"] is True
    assert [call[1] for call in calls] == [
        "/repos/o/r/issues/51",
        "/search/issues",
        "/repos/o/r/issues/51",
    ]


def test_execute_mutation_rejects_malformed_proposal_without_api_call() -> None:
    with patch.object(GitHubRestClient, "request") as request:
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal={"operation": "create"}, github_token="tok"
        )

    request.assert_not_called()
    assert result["executed"] is False
    assert result["side_effect"] == "github_issue_mutation_rejected"
    assert "missing required field" in result["error"]


def test_execute_mutation_rejects_payload_without_marker() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="create",
        slack_text="ship this",
        slack_url="https://slack.example/archives/C/p1",
    )["proposal"]
    proposal = {**proposal, "payload": {**proposal["payload"], "body": "missing marker"}}

    with patch.object(GitHubRestClient, "request") as request:
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    request.assert_not_called()
    assert result["executed"] is False
    assert result["side_effect"] == "github_issue_mutation_rejected"
    assert "idempotency marker" in result["error"]


def test_execute_mutation_returns_api_errors() -> None:
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="close",
        issue_number=51,
        slack_text="done",
    )["proposal"]

    with patch.object(
        GitHubRestClient, "request", side_effect=GitHubApiError("nope", status_code=403)
    ):
        result = execute_github_issue_mutation(
            owner="o", repo="r", proposal=proposal, github_token="tok"
        )

    assert result["available"] is False
    assert result["executed"] is False
    assert "nope" in result["error"]


def test_requires_approval_blocks_without_hook() -> None:
    tool: RegisteredTool = _registered_tool(execute_github_issue_mutation)
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="close",
        issue_number=51,
        slack_text="done",
    )["proposal"]

    result = execute_tool_calls(
        [
            ToolCall(
                id="c1",
                name="execute_github_issue_mutation",
                input={"owner": "o", "repo": "r", "proposal": proposal},
            )
        ],
        [tool],
        {},
    )[0]

    assert result.is_error is True
    assert result.details["approval_required"] is True


def test_requires_approval_allows_runtime_approval_hook() -> None:
    tool: RegisteredTool = _registered_tool(execute_github_issue_mutation)
    proposal = propose_github_issue_mutation_from_slack(
        owner="o",
        repo="r",
        operation="close",
        issue_number=51,
        slack_text="done",
    )["proposal"]

    def approve(request: ToolExecutionRequest) -> BeforeToolCallResult:
        assert request.tool.requires_approval is True
        return BeforeToolCallResult(approved=True)

    with patch.object(GitHubRestClient, "request", return_value={"number": 51}):
        result = execute_tool_calls(
            [
                ToolCall(
                    id="c1",
                    name="execute_github_issue_mutation",
                    input={"owner": "o", "repo": "r", "proposal": proposal},
                )
            ],
            [tool],
            {},
            hooks=ToolExecutionHooks(before_tool_call=approve),
        )[0]

    assert result.is_error is False
