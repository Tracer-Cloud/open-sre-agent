"""GitHub MCP-backed issue search tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.code_host_unavailable import code_host_unavailable_payload
from integrations.github.helpers import (
    github_creds,
    github_source_available,
    normalize_github_tool_result,
    resolve_github_mcp_config,
)
from integrations.github_mcp import (
    build_github_issue_search_query,
    call_github_mcp_tool,
)


def _search_github_issues_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "query": gh.get("query") or "crash OR error OR exception",
        "state": gh.get("state", "open"),
        **github_creds(gh),
    }


def _search_github_issues_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


@tool(
    name="search_github_issues",
    source="github",
    description="Search GitHub repository issues through the configured GitHub MCP server.",
    use_cases=[
        "Investigating crash, error, or exception reports filed as issues",
        "Checking known issues for a repository or platform during an incident",
        "Finding related bug reports that may explain a failure",
    ],
    requires=["owner", "repo", "query"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "query": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
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
    state: str = "open",
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search GitHub repository issues through the configured GitHub MCP server."""
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

    final_query = build_github_issue_search_query(owner, repo, query, state)
    result = call_github_mcp_tool(config, "search_issues", {"query": final_query})
    payload = normalize_github_tool_result(result)
    payload["issues"] = payload.pop("structured_content", None)
    payload["query"] = final_query
    return payload
