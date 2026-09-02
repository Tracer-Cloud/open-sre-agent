"""Azure SQL Wait Stats Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_wait_stats,
    resolve_azure_sql_config,
)


def _map_get_azure_sql_wait_stats(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the wait-type count and the top wait by total wait time."""
    if not output.get("available"):
        return
    waits = output.get("waits") or []
    if not isinstance(waits, list) or not waits:
        return
    parts = [f"{len(waits)} wait type(s)"]
    top = max(
        waits,
        key=lambda w: (
            w.get("wait_time_ms") if isinstance(w.get("wait_time_ms"), (int, float)) else -1
        ),
    )
    top_type = top.get("wait_type")
    top_ms = top.get("wait_time_ms")
    if top_type and isinstance(top_ms, (int, float)):
        parts.append(f"top {top_type} {top_ms:.0f}ms")
    record_evidence_entry(
        evidence,
        source="get_azure_sql_wait_stats",
        label="Azure SQL Wait Stats",
        summary=", ".join(parts),
    )


@tool(
    name="get_azure_sql_wait_stats",
    description="Retrieve top wait statistics from Azure SQL Database to diagnose throttling, lock contention, IO bottlenecks, and network issues.",
    source="azure_sql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Identifying the most impactful wait types during an incident",
        "Diagnosing lock contention or IO bottlenecks",
        "Understanding resource governance limits on Azure SQL",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
    evidence_mapper=_map_get_azure_sql_wait_stats,
)
def get_azure_sql_wait_stats(
    server: str,
    database: str | None = None,
    port: int = 1433,
) -> dict[str, Any]:
    """Fetch wait statistics from an Azure SQL Database instance."""
    _db_defaulted = database is None
    if database is None:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_wait_stats(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
        )
    return result
