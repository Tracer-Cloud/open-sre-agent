"""PostgreSQL Table Stats Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_table_stats,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


def _map_get_postgresql_table_stats(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the table count and the largest table by total size."""
    if not output.get("available"):
        return
    tables = output.get("tables") or []
    if not isinstance(tables, list) or not tables:
        return
    parts = [f"{len(tables)} table(s)"]
    largest = max(
        tables,
        key=lambda t: (
            t.get("size", {}).get("total_mb")
            if isinstance(t.get("size", {}).get("total_mb"), (int, float))
            else -1
        ),
    )
    largest_name = largest.get("table_name")
    largest_mb = largest.get("size", {}).get("total_mb")
    if largest_name and isinstance(largest_mb, (int, float)):
        parts.append(f"largest {largest_name} {largest_mb:.0f}MB")
    record_evidence_entry(
        evidence,
        source="get_postgresql_table_stats",
        label="PostgreSQL Table Stats",
        summary=", ".join(parts),
    )


@tool(
    name="get_postgresql_table_stats",
    description="Retrieve PostgreSQL table statistics including size, row counts, index usage, and maintenance info.",
    source="postgresql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Identifying large tables or rapid table growth during storage incidents",
        "Analyzing table scan patterns and index usage efficiency",
        "Checking table maintenance status like vacuum and analyze operations",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
    evidence_mapper=_map_get_postgresql_table_stats,
)
def get_postgresql_table_stats(
    host: str,
    database: str | None = None,
    schema_name: str = "public",
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch table statistics for a specific schema (default 'public')."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_table_stats(config, schema_name=schema_name),
    )
