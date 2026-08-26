"""Bitbucket Search Code Tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from infrastructure.text.truncation import truncate
from integrations.bitbucket.client import search_code
from integrations.bitbucket.config import (
    BitbucketConfig,
    bitbucket_config_from_env,
    build_bitbucket_config,
)
from integrations.bitbucket.tools.availability import bitbucket_available_or_backend

#: Bound the search query text echoed into a report summary -- unbounded and
#: caller-supplied.
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def _map_search_bitbucket_code(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the match count and search query, qualifying page-capped totals."""
    if not output.get("available"):
        return
    results = output.get("results") or []
    if not results:
        return
    total = output.get("total_returned", len(results))
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    summary = f"{count_label} match(es)"
    query = output.get("query")
    if query:
        safe_query = truncate(str(query).replace("\n", " "), _QUERY_SUMMARY_TRUNCATE_LEN)
        summary += f" for '{safe_query}'"
    record_evidence_entry(
        evidence,
        source="search_bitbucket_code",
        label="Bitbucket Code Search",
        summary=summary,
    )


def _resolve_config(
    workspace: str | None,
    username: str | None,
    app_password: str | None,
    base_url: str | None = None,
    max_results: int | None = None,
    integration_id: str | None = None,
) -> BitbucketConfig | None:
    env_config = bitbucket_config_from_env()
    if any([workspace, username, app_password, base_url, max_results, integration_id]):
        return build_bitbucket_config(
            {
                "workspace": workspace or (env_config.workspace if env_config else ""),
                "username": username or (env_config.username if env_config else ""),
                "app_password": app_password or (env_config.app_password if env_config else ""),
                "base_url": base_url or (env_config.base_url if env_config else ""),
                "max_results": max_results or (env_config.max_results if env_config else 25),
                "integration_id": integration_id
                or (env_config.integration_id if env_config else ""),
            }
        )
    return env_config


def _bb_creds(bb: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace": bb.get("workspace"),
        "username": bb.get("username"),
        "app_password": bb.get("app_password"),
        "base_url": bb.get("base_url"),
        "max_results": bb.get("max_results"),
        "integration_id": bb.get("integration_id"),
    }


def _search_bitbucket_code_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    bb = sources["bitbucket"]
    return {
        "query": bb.get("query") or "exception OR error",
        "repo_slug": bb.get("repo_slug", ""),
        "limit": 20,
        **_bb_creds(bb),
    }


def _search_bitbucket_code_available(sources: dict[str, dict]) -> bool:
    return bitbucket_available_or_backend(sources)


@tool(
    name="search_bitbucket_code",
    description="Search code across a Bitbucket workspace or specific repository.",
    source="bitbucket",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Finding where a specific function or configuration is defined",
        "Searching for error patterns across repositories",
    ],
    requires=["query"],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo_slug": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 20},
            "workspace": {"type": "string"},
            "username": {"type": "string"},
            "app_password": {"type": "string"},
            "base_url": {"type": "string"},
            "max_results": {"type": "integer"},
            "integration_id": {"type": "string"},
        },
        "required": ["query"],
    },
    is_available=_search_bitbucket_code_available,
    extract_params=_search_bitbucket_code_extract_params,
    evidence_mapper=_map_search_bitbucket_code,
)
def search_bitbucket_code(
    query: str,
    workspace: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
    base_url: str | None = None,
    max_results: int | None = None,
    integration_id: str | None = None,
    repo_slug: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search code in a Bitbucket workspace."""
    config = _resolve_config(
        workspace,
        username,
        app_password,
        base_url,
        max_results,
        integration_id,
    )
    if config is None:
        return code_host_unavailable_payload(
            source="bitbucket",
            integration_name="Bitbucket",
            empty_key="results",
            empty_value=[],
        )
    return search_code(config, query=query, repo_slug=repo_slug, limit=limit)
