"""PostgreSQL Current Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_current_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


def _map_get_postgresql_current_queries(
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
        source="get_postgresql_current_queries",
        label="PostgreSQL Current Queries",
        summary=", ".join(parts),
    )


@tool(
    name="get_postgresql_current_queries",
    description=(
        "Retrieve currently executing PostgreSQL queries above a specific duration threshold."
    ),
    source="postgresql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Identifying long-running queries that may be causing performance issues",
        "Investigating database locks and blocking queries during incidents",
        "Finding resource-intensive queries correlating with alert timeframes",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
    evidence_mapper=_map_get_postgresql_current_queries,
)
def get_postgresql_current_queries(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_current_queries(config, threshold_seconds=threshold_seconds),
    )
