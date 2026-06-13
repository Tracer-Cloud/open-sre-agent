"""GitHub MCP-backed issue and PR search tool."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import call_github_mcp_tool
from app.tools.tool_decorator import tool
from app.tools.utils.code_host_unavailable import code_host_unavailable_payload
from app.tools.utils.github_helpers import (
    github_creds,
    github_source_available,
    normalize_github_tool_result,
    resolve_github_mcp_config,
)


def _search_github_issues_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "query": gh.get("query") or "bug",
        **github_creds(gh),
    }


def _search_github_issues_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def build_github_issue_search_query(owner: str, repo: str, query: str) -> str:
    """Build a repo-scoped GitHub issue/PR search query."""
    repo_qualifier = f"repo:{owner}/{repo}"
    query = query.strip()
    if repo_qualifier in query:
        return query
    return f"{query} {repo_qualifier}".strip()


@tool(
    name="search_github_issues",
    source="github",
    description="Search GitHub repository issues and pull requests through the configured GitHub MCP server.",
    use_cases=[
        "Finding recent bug reports or issues related to an incident",
        "Checking if a similar problem has been discussed in tickets",
        "Searching for recently merged pull requests or commits associated with a stack trace",
    ],
    requires=["owner", "repo", "query"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "query": {"type": "string"},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "query"],
    },
    is_available=_search_github_issues_available,
    extract_params=_search_github_issues_extract_params,
)
def search_github_issues(
    owner: str,
    repo: str,
    query: str,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search GitHub repository issues and pull requests through the configured GitHub MCP server."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub MCP",
            empty_key="issues",
            empty_value=[],
        )

    final_query = build_github_issue_search_query(owner, repo, query)
    result = call_github_mcp_tool(config, "search_issues", {"query": final_query})
    payload = normalize_github_tool_result(result)
    payload["issues"] = payload.pop("structured_content", None) or []
    payload["query"] = final_query
    return payload
