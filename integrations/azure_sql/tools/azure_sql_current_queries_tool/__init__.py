"""Azure SQL Current Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_current_queries,
    resolve_azure_sql_config,
)


def _map_get_azure_sql_current_queries(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the count of long-running queries and their longest duration."""
    if not output.get("available"):
        return
    queries = output.get("queries") or []
    if not isinstance(queries, list) or not queries:
        return
    parts = [f"{len(queries)} running query/queries"]
    durations = [
        q.get("duration_seconds")
        for q in queries
        if isinstance(q.get("duration_seconds"), (int, float))
    ]
    if durations:
        parts.append(f"max duration {max(durations)}s")
    record_evidence_entry(
        evidence,
        source="get_azure_sql_current_queries",
        label="Azure SQL Current Queries",
        summary=", ".join(parts),
    )


@tool(
    name="get_azure_sql_current_queries",
    description=(
        "Retrieve currently running queries on Azure SQL Database above a duration"
        " threshold, including wait types and resource usage."
    ),
    source="azure_sql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Identifying long-running queries causing lock contention",
        "Diagnosing blocking chains during an Azure SQL incident",
        "Finding queries consuming excessive CPU or IO",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
    evidence_mapper=_map_get_azure_sql_current_queries,
)
def get_azure_sql_current_queries(
    server: str,
    database: str | None = None,
    port: int = 1433,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Fetch currently running queries from an Azure SQL Database instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="master",
        config_resolver=resolve_azure_sql_config,
        resolver_kwargs={"server": server, "port": port},
        db_caller=lambda config: get_current_queries(config, threshold_seconds=threshold_seconds),
    )
