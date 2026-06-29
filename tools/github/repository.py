"""GitHub REST repository metadata tools."""

from __future__ import annotations

from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from tools._telemetry import report_run_error
from tools.tool_decorator import tool
from tools.utils.github_helpers import github_creds, github_source_available


def _github_repository_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or resolve_github_token(None))
        and gh.get("owner")
        and gh.get("repo")
    )


def _github_repository_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


def _normalize_repository(repo: dict[str, Any], *, owner: str, repo_name: str) -> dict[str, Any]:
    raw_license = repo.get("license")
    license_info: dict[str, Any] = raw_license if isinstance(raw_license, dict) else {}
    return {
        "full_name": str(repo.get("full_name") or f"{owner}/{repo_name}"),
        "html_url": str(repo.get("html_url") or ""),
        "description": str(repo.get("description") or ""),
        "default_branch": str(repo.get("default_branch") or ""),
        "visibility": str(
            repo.get("visibility") or ("private" if repo.get("private") else "public")
        ),
        "stargazers_count": repo.get("stargazers_count"),
        "watchers_count": repo.get("watchers_count"),
        "forks_count": repo.get("forks_count"),
        "open_issues_count": repo.get("open_issues_count"),
        "subscribers_count": repo.get("subscribers_count"),
        "language": str(repo.get("language") or ""),
        "topics": list(repo.get("topics") or []),
        "license": str(license_info.get("spdx_id") or license_info.get("name") or ""),
        "created_at": str(repo.get("created_at") or ""),
        "updated_at": str(repo.get("updated_at") or ""),
        "pushed_at": str(repo.get("pushed_at") or ""),
        "archived": bool(repo.get("archived")),
        "disabled": bool(repo.get("disabled")),
    }


@tool(
    name="get_github_repository",
    source="github",
    description=(
        "Fetch GitHub repository metadata such as star count, forks, open issues, "
        "description, default branch, and visibility via the GitHub REST API."
    ),
    use_cases=[
        "Answering how many GitHub stars a repository has",
        "Reporting repository metadata for status or community questions",
        "Checking repo visibility, default branch, or activity timestamps",
    ],
    anti_examples=[
        "Searching repository source code (use search_github_code)",
        "Searching GitHub issues by keyword (use search_github_issues)",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_repository_available,
    extract_params=_github_repository_extract_params,
)
def get_github_repository(
    owner: str,
    repo: str,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch repository metadata from the GitHub REST API."""
    try:
        payload = GitHubRestClient(github_token).request("GET", f"/repos/{owner}/{repo}")
    except GitHubApiError as exc:
        report_run_error(
            exc,
            tool_name="get_github_repository",
            source="github",
            component="tools.github.repository",
            method="GitHubRestClient.request",
            extras={"owner": owner, "repo": repo},
        )
        return {
            "source": "github",
            "available": False,
            "error": str(exc),
            "owner": owner,
            "repo": repo,
            "repository": {},
        }
    if not isinstance(payload, dict):
        return {
            "source": "github",
            "available": False,
            "error": "GitHub API returned an unexpected repository payload.",
            "owner": owner,
            "repo": repo,
            "repository": {},
        }
    repository = _normalize_repository(payload, owner=owner, repo_name=repo)
    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "repository": repository,
        "stargazers_count": repository["stargazers_count"] or 0,
    }
