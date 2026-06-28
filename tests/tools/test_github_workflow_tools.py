"""Tests for GitHub-backed work/status/community/task tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.community_followup_tool import summarize_community_followups
from tools.github.work_status import (
    list_github_security_alerts,
    list_github_work_items,
    summarize_github_pr_status,
)
from tools.github.workflow_skill import (
    build_slack_task_payload,
    build_work_status_report,
    summarize_community_followups_from_comments,
)
from tools.slack_task_tool import (
    close_github_task_from_slack,
    create_github_task_from_slack,
    update_github_task_from_slack,
)
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


class TestGenerateWorkStatusReportContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(generate_work_status_report)


class TestSummarizeCommunityFollowupsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(summarize_community_followups)


class TestCreateGitHubTaskFromSlackContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(create_github_task_from_slack)


class TestUpdateGitHubTaskFromSlackContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(update_github_task_from_slack)


class TestCloseGitHubTaskFromSlackContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(close_github_task_from_slack)


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
        {
            "number": 3,
            "title": "PR returned from issues endpoint",
            "state": "open",
            "html_url": "https://github.com/o/r/pull/3",
            "user": {"login": "dan"},
            "assignees": [],
            "labels": [],
            "pull_request": {},
            "updated_at": "2026-06-28T12:00:00Z",
        },
    ]
    with patch("tools.github.work_status._github_api_request", return_value=issues):
        result = list_github_work_items(owner="o", repo="r", github_token="tok")

    assert result["available"] is True
    assert result["counts"] == {"total": 2, "taken": 1, "up_for_grabs": 1, "unassigned": 0}
    assert [item["work_status"] for item in result["items"]] == ["taken", "up_for_grabs"]


def test_summarize_github_pr_status_marks_blocked_by_failed_checks() -> None:
    pulls = [
        {
            "number": 10,
            "title": "Ready PR",
            "draft": False,
            "html_url": "https://github.com/o/r/pull/10",
            "user": {"login": "alice"},
            "head": {"sha": "abc", "ref": "feature"},
            "mergeable": True,
            "mergeable_state": "clean",
            "updated_at": "2026-06-28T10:00:00Z",
        },
        {
            "number": 11,
            "title": "Failing PR",
            "draft": False,
            "html_url": "https://github.com/o/r/pull/11",
            "user": {"login": "bob"},
            "head": {"sha": "def", "ref": "bugfix"},
            "mergeable": True,
            "mergeable_state": "clean",
            "updated_at": "2026-06-28T11:00:00Z",
        },
    ]
    checks = {
        "/repos/o/r/commits/abc/check-runs": {
            "check_runs": [{"name": "test", "conclusion": "success", "status": "completed"}]
        },
        "/repos/o/r/commits/def/check-runs": {
            "check_runs": [{"name": "test", "conclusion": "failure", "status": "completed"}]
        },
    }

    def fake_request(_method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/repos/o/r/pulls":
            return pulls
        return checks[path]

    with patch("tools.github.work_status._github_api_request", side_effect=fake_request):
        result = summarize_github_pr_status(owner="o", repo="r", github_token="tok")

    assert result["counts"]["mergeable"] == 1
    assert result["counts"]["blocked"] == 1
    assert result["pull_requests"][1]["status"] == "blocked"
    assert result["pull_requests"][1]["blocking_reasons"] == ["failed checks: test"]


def test_list_github_security_alerts_merges_requested_alert_types() -> None:
    def fake_request(_method: str, path: str, **_kwargs: Any) -> Any:
        if path.endswith("/dependabot/alerts"):
            return [{"number": 1, "state": "open", "security_advisory": {"summary": "dep"}}]
        if path.endswith("/secret-scanning/alerts"):
            return [{"number": 2, "state": "open", "secret_type": "token"}]
        if path.endswith("/code-scanning/alerts"):
            return [{"number": 3, "state": "open", "rule": {"description": "code"}}]
        raise AssertionError(path)

    with patch("tools.github.work_status._github_api_request", side_effect=fake_request):
        result = list_github_security_alerts(
            owner="o", repo="r", alert_type="all", github_token="tok"
        )

    assert result["counts"] == {
        "dependabot": 1,
        "secret_scanning": 1,
        "code_scanning": 1,
        "total": 3,
    }
    assert {alert["type"] for alert in result["alerts"]} == {
        "dependabot",
        "secret_scanning",
        "code_scanning",
    }


def test_generate_work_status_report_is_read_only_summary() -> None:
    result = generate_work_status_report(
        work_items=[
            {"number": 1, "title": "Assigned bug", "work_status": "taken", "assignees": ["bob"]},
            {"number": 2, "title": "Starter task", "work_status": "up_for_grabs", "assignees": []},
        ],
        pull_requests=[
            {
                "number": 10,
                "title": "Failing PR",
                "status": "blocked",
                "blocking_reasons": ["failed checks: test"],
            },
            {"number": 11, "title": "Ready PR", "status": "mergeable", "blocking_reasons": []},
        ],
    )

    assert result["side_effects"] == []
    assert result["counts"]["open_work"] == 2
    assert result["counts"]["blocked_prs"] == 1
    assert "Starter task" in result["slack_markdown"]
    assert "Failing PR" in result["slack_markdown"]


def test_workflow_skill_builds_same_report_without_tool_io() -> None:
    result = build_work_status_report(
        work_items=[
            {"number": 2, "title": "Starter task", "work_status": "up_for_grabs", "assignees": []}
        ],
        pull_requests=[
            {
                "number": 10,
                "title": "Failing PR",
                "status": "blocked",
                "blocking_reasons": ["failed checks: test"],
            }
        ],
        context="morning",
    )

    assert result["counts"] == {
        "open_work": 1,
        "taken": 0,
        "up_for_grabs": 1,
        "unassigned": 0,
        "blocked_prs": 1,
        "mergeable_prs": 0,
    }
    assert result["side_effects"] == []
    assert "*Engineering status — morning*" in result["slack_markdown"]


def test_summarize_community_followups_finds_unanswered_questions() -> None:
    comments = [
        {
            "issue_number": 7,
            "issue_title": "Meeting",
            "author": "contributor",
            "body": "When is the community meeting?",
            "created_at": "2026-06-28T10:00:00Z",
            "url": "u1",
        },
        {
            "issue_number": 8,
            "issue_title": "Agenda",
            "author": "maintainer",
            "body": "Agenda item: release demo",
            "created_at": "2026-06-28T10:05:00Z",
            "url": "u2",
        },
    ]

    result = summarize_community_followups(comments=comments, maintainer_logins=["maintainer"])

    assert result["unanswered_questions"][0]["issue_number"] == 7
    assert result["agenda_items"][0]["body"] == "Agenda item: release demo"
    assert "When is the community meeting?" in result["suggested_replies"][0]["context"]


def test_workflow_skill_summarizes_followups_without_tool_io() -> None:
    result = summarize_community_followups_from_comments(
        comments=[
            {
                "issue_number": 7,
                "issue_title": "Meeting",
                "author": "contributor",
                "body": "Could someone share the agenda?",
                "created_at": "2026-06-28T10:00:00Z",
                "url": "u1",
            }
        ],
        maintainer_logins=["maintainer"],
    )

    assert result["counts"]["unanswered_questions"] == 1
    assert result["suggested_replies"][0]["issue_number"] == 7
    assert result["side_effects"] == []


def test_slack_task_create_requires_confirmation_before_mutation() -> None:
    with patch("tools.slack_task_tool._github_api_request") as request:
        result = create_github_task_from_slack(
            owner="o",
            repo="r",
            slack_text="add this to the hackathon list",
            slack_url="https://slack.example/archives/C/p1",
            github_token="tok",
        )

    request.assert_not_called()
    assert result["executed"] is False
    assert result["side_effect"] == "would_create_github_issue"
    assert "slack.example" in result["issue"]["body"]


def test_workflow_skill_builds_slack_task_payload_without_side_effects() -> None:
    payload = build_slack_task_payload(
        operation="create",
        slack_text="add this to the hackathon list",
        slack_url="https://slack.example/archives/C/p1",
        labels=["hackathon"],
    )

    assert payload["title"] == "add this to the hackathon list"
    assert payload["labels"] == ["hackathon"]
    assert "https://slack.example/archives/C/p1" in payload["body"]


def test_slack_task_create_executes_with_confirmation() -> None:
    created = {
        "number": 99,
        "html_url": "https://github.com/o/r/issues/99",
        "title": "Hackathon task",
    }
    with patch("tools.slack_task_tool._github_api_request", return_value=created) as request:
        result = create_github_task_from_slack(
            owner="o",
            repo="r",
            slack_text="add this to the hackathon list",
            slack_url="https://slack.example/archives/C/p1",
            title="Hackathon task",
            github_token="tok",
            confirm=True,
        )

    request.assert_called_once()
    assert result["executed"] is True
    assert result["issue"]["number"] == 99


def test_slack_task_update_and_close_are_confirmation_gated() -> None:
    with patch("tools.slack_task_tool._github_api_request") as request:
        update_result = update_github_task_from_slack(
            owner="o",
            repo="r",
            issue_number=51,
            slack_text="PR shipped",
            slack_url="https://slack.example/archives/C/p2",
            github_token="tok",
        )
        close_result = close_github_task_from_slack(
            owner="o",
            repo="r",
            issue_number=51,
            slack_text="done",
            slack_url="https://slack.example/archives/C/p3",
            github_token="tok",
        )

    request.assert_not_called()
    assert update_result["side_effect"] == "would_update_github_issue"
    assert close_result["side_effect"] == "would_close_github_issue"
