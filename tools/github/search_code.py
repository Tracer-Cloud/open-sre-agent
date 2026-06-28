"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from integrations.github_mcp import (
    build_github_code_search_query,
    call_github_mcp_tool,
)
from tools.tool_decorator import tool
from tools.utils.code_host_unavailable import code_host_unavailable_payload
from tools.utils.github_helpers import (
    github_creds,
    github_source_available,
    normalize_github_tool_result,
    resolve_github_mcp_config,
)


def _search_github_code_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "query": gh.get("query") or "exception OR error",
        **github_creds(gh),
    }


def _search_github_code_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


@tool(
    name="search_github_code",
    source="github",
    description="Search GitHub repository code through the configured GitHub MCP server.",
    use_cases=[
        "Investigating alerts that mention a repository, branch, or commit",
        "Finding source code related to failures, exceptions, and stack frames",
        "Tracing config, workflow, or application code that may explain an incident",
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
    is_available=_search_github_code_available,
    extract_params=_search_github_code_extract_params,
)
def search_github_code(
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
    """Search GitHub repository code through the configured GitHub MCP server."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub MCP",
            empty_key="matches",
            empty_value=[],
        )

    final_query = build_github_code_search_query(owner, repo, query)
    result = call_github_mcp_tool(config, "search_code", {"query": final_query})
    payload = normalize_github_tool_result(result)
    payload["matches"] = payload.pop("structured_content", None)
    payload["query"] = final_query
    return payload
