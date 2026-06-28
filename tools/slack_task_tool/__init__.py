"""GitHub-backed task mutation helpers for explicit Slack requests."""

from __future__ import annotations

from typing import Any

from tools.github.work_status import _github_api_request
from tools.github.workflow_skill import (
    build_slack_task_payload,
    dry_run_slack_task_result,
    slack_task_failure,
    slack_task_success,
)
from tools.tool_decorator import tool
from tools.utils.github_helpers import github_creds, github_source_available


def _task_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def _task_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


@tool(
    name="create_github_task_from_slack",
    source="github",
    description="Create a GitHub issue from an explicit Slack task request, preserving the Slack source link; requires confirm=true.",
    use_cases=[
        "Turning an explicit Slack request into a GitHub-backed task",
        "Preserving Slack thread/message URLs in issue bodies",
        "Creating hackathon/task-list issues only after confirmation",
    ],
    anti_examples=[
        "Inferring tasks from casual Slack discussion",
        "Creating issues without explicit confirmation",
    ],
    surfaces=("chat",),
    side_effect_level="mutating",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "slack_text": {"type": "string"},
            "slack_url": {"type": "string"},
            "title": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "assignees": {"type": "array", "items": {"type": "string"}},
            "confirm": {"type": "boolean"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "slack_text"],
    },
    is_available=_task_available,
    extract_params=_task_extract_params,
)
def create_github_task_from_slack(
    owner: str,
    repo: str,
    slack_text: str,
    slack_url: str = "",
    title: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    confirm: bool = False,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    payload = build_slack_task_payload(
        operation="create",
        slack_text=slack_text,
        slack_url=slack_url,
        title=title,
        labels=labels,
        assignees=assignees,
    )
    if not confirm:
        return dry_run_slack_task_result("would_create_github_issue", payload)
    try:
        issue = _github_api_request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            github_token=github_token,
            body=payload,
        )
    except RuntimeError as exc:
        return slack_task_failure("create_github_issue_failed", exc)
    return slack_task_success("created_github_issue", issue)


@tool(
    name="update_github_task_from_slack",
    source="github",
    description="Update a GitHub issue from an explicit Slack task follow-up; requires confirm=true.",
    use_cases=[
        "Adding Slack follow-up context to an existing GitHub-backed task",
        "Updating labels, assignees, title, or body from an explicit Slack request",
    ],
    anti_examples=["Updating GitHub from ambiguous Slack chatter", "Closing issues"],
    surfaces=("chat",),
    side_effect_level="mutating",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "issue_number": {"type": "integer"},
            "slack_text": {"type": "string"},
            "slack_url": {"type": "string"},
            "title": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "assignees": {"type": "array", "items": {"type": "string"}},
            "confirm": {"type": "boolean"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "issue_number", "slack_text"],
    },
    is_available=_task_available,
    extract_params=_task_extract_params,
)
def update_github_task_from_slack(
    owner: str,
    repo: str,
    issue_number: int,
    slack_text: str,
    slack_url: str = "",
    title: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    confirm: bool = False,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    payload = build_slack_task_payload(
        operation="update",
        issue_number=issue_number,
        slack_text=slack_text,
        slack_url=slack_url,
        title=title,
        labels=labels,
        assignees=assignees,
    )
    if not confirm:
        return dry_run_slack_task_result("would_update_github_issue", payload)
    payload_for_api = {key: value for key, value in payload.items() if key != "number"}
    try:
        issue = _github_api_request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            github_token=github_token,
            body=payload_for_api,
        )
    except RuntimeError as exc:
        return slack_task_failure("update_github_issue_failed", exc)
    return slack_task_success("updated_github_issue", issue)


@tool(
    name="close_github_task_from_slack",
    source="github",
    description="Close a GitHub issue from an explicit Slack task completion request; requires confirm=true.",
    use_cases=[
        "Closing a GitHub-backed task when Slack explicitly says it shipped or is done",
        "Preserving the Slack completion source in the close body",
    ],
    anti_examples=["Closing tasks without confirmation", "Closing PRs"],
    surfaces=("chat",),
    side_effect_level="mutating",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "issue_number": {"type": "integer"},
            "slack_text": {"type": "string"},
            "slack_url": {"type": "string"},
            "confirm": {"type": "boolean"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "issue_number", "slack_text"],
    },
    is_available=_task_available,
    extract_params=_task_extract_params,
)
def close_github_task_from_slack(
    owner: str,
    repo: str,
    issue_number: int,
    slack_text: str,
    slack_url: str = "",
    confirm: bool = False,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    payload = build_slack_task_payload(
        operation="close",
        issue_number=issue_number,
        slack_text=slack_text,
        slack_url=slack_url,
    )
    if not confirm:
        return dry_run_slack_task_result("would_close_github_issue", payload)
    payload_for_api = {key: value for key, value in payload.items() if key != "number"}
    try:
        issue = _github_api_request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            github_token=github_token,
            body=payload_for_api,
        )
    except RuntimeError as exc:
        return slack_task_failure("close_github_issue_failed", exc)
    return slack_task_success("closed_github_issue", issue)
