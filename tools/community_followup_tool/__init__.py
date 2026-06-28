"""Community and contributor follow-up summary tool."""

from __future__ import annotations

from typing import Any

from tools.github.work_status import _github_api_request
from tools.github.workflow_skill import (
    normalize_community_comment,
    summarize_community_followups_from_comments,
)
from tools.tool_decorator import tool
from tools.utils.github_helpers import github_creds, github_source_available


def _community_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def _community_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


def _fetch_issue_comments(
    owner: str,
    repo: str,
    *,
    max_issues: int,
    github_token: str | None,
) -> list[dict[str, Any]]:
    issue_payload = _github_api_request(
        "GET",
        f"/repos/{owner}/{repo}/issues",
        github_token=github_token,
        params={"state": "open", "per_page": max(1, min(max_issues, 100))},
    )
    issues = (
        [item for item in issue_payload if isinstance(item, dict) and "pull_request" not in item]
        if isinstance(issue_payload, list)
        else []
    )
    comments: list[dict[str, Any]] = []
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        comment_payload = _github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            github_token=github_token,
            params={"per_page": 100},
        )
        if isinstance(comment_payload, list):
            comments.extend(
                normalize_community_comment(comment, issue)
                for comment in comment_payload
                if isinstance(comment, dict)
            )
    return comments


@tool(
    name="summarize_community_followups",
    source="github",
    description="Summarize unanswered community questions, meeting agenda items, and suggested replies from GitHub issue comments.",
    use_cases=[
        "Finding unanswered contributor questions in GitHub issue comments",
        "Preparing community meeting agenda follow-ups",
        "Drafting suggested replies without mutating GitHub or messaging platforms",
    ],
    anti_examples=["Posting replies", "Changing GitHub labels or assignees"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "comments": {"type": "array"},
            "maintainer_logins": {"type": "array", "items": {"type": "string"}},
            "max_issues": {"type": "integer"},
            "github_token": {"type": "string"},
        },
        "required": [],
    },
    is_available=_community_available,
    extract_params=_community_extract_params,
)
def summarize_community_followups(
    owner: str = "",
    repo: str = "",
    comments: list[dict[str, Any]] | None = None,
    maintainer_logins: list[str] | None = None,
    max_issues: int = 25,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    try:
        normalized_comments = (
            [normalize_community_comment(comment) for comment in comments]
            if comments is not None
            else _fetch_issue_comments(
                owner, repo, max_issues=max_issues, github_token=github_token
            )
        )
    except RuntimeError as exc:
        return {
            "source": "github",
            "available": False,
            "error": str(exc),
            "unanswered_questions": [],
            "agenda_items": [],
            "suggested_replies": [],
            "side_effects": [],
        }

    summary = summarize_community_followups_from_comments(
        comments=normalized_comments,
        maintainer_logins=maintainer_logins,
    )
    return {"source": "github", "available": True, **summary}
