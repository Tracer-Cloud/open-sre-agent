"""Community and contributor follow-up summary tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import github_creds, github_source_available
from integrations.github.tools.workflow import summarize_community_followups_from_comments


def _community_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or resolve_github_token(None))
        and gh.get("owner")
        and gh.get("repo")
    )


def _community_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


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
            "per_page": {"type": "integer"},
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
    per_page: int = 100,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    try:
        normalized_comments = (
            comments
            if comments is not None
            else GitHubRestClient(github_token).paginate(
                f"/repos/{owner}/{repo}/issues/comments",
                params={"per_page": max(1, min(per_page, 100))},
            )
        )
    except GitHubApiError as exc:
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
