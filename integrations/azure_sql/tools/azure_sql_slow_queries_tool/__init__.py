"""Azure SQL Slow Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_slow_queries,
    resolve_azure_sql_config,
)


def _map_get_azure_sql_slow_queries(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the slow-query count and the slowest average elapsed time."""
    if not output.get("available"):
        return
    queries = output.get("queries") or []
    if not isinstance(queries, list) or not queries:
        return
    parts = [f"{len(queries)} slow query/queries"]
    averages = [
        q.get("avg_time_ms") for q in queries if isinstance(q.get("avg_time_ms"), (int, float))
    ]
    if averages:
        parts.append(f"slowest avg {max(averages):.0f}ms")
    record_evidence_entry(
        evidence,
        source="get_azure_sql_slow_queries",
        label="Azure SQL Slow Queries",
        summary=", ".join(parts),
    )


@tool(
    name="get_azure_sql_slow_queries",
    description=(
        "Retrieve slow query statistics from Azure SQL Database query stats DMV,"
        " ordered by average elapsed time."
    ),
    source="azure_sql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Identifying queries with high average execution time",
        "Finding resource-intensive queries causing DTU throttling",
        "Reviewing query performance trends for capacity planning",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
    evidence_mapper=_map_get_azure_sql_slow_queries,
)
def get_azure_sql_slow_queries(
    server: str,
    database: str | None = None,
    port: int = 1433,
    threshold_ms: int = 1000,
) -> dict[str, Any]:
    """Fetch slow query statistics from an Azure SQL Database instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="master",
        config_resolver=resolve_azure_sql_config,
        resolver_kwargs={"server": server, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
