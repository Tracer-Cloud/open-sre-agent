"""PostgreSQL Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_server_status,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


def _map_get_postgresql_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite connections, cache hit ratio, and uptime."""
    if not output.get("available"):
        return
    connections = output.get("connections") or {}
    db_stats = output.get("database_stats") or {}
    parts = []
    total = connections.get("total")
    if isinstance(total, int):
        parts.append(f"{total} connection(s)")
    if isinstance(connections.get("active"), int):
        parts.append(f"{connections.get('active')} active")
    hit_ratio = db_stats.get("cache_hit_ratio_percent")
    if isinstance(hit_ratio, (int, float)):
        parts.append(f"cache hit {hit_ratio}%")
    uptime = output.get("uptime")
    if uptime:
        parts.append(f"uptime {uptime}")
    if not parts:
        return
    record_evidence_entry(
        evidence,
        source="get_postgresql_server_status",
        label="PostgreSQL Server Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_postgresql_server_status",
    description="Retrieve PostgreSQL server metrics including connections, transactions, cache hit ratio, and database statistics.",
    source="postgresql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Checking PostgreSQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing transaction rates and cache efficiency metrics",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
    evidence_mapper=_map_get_postgresql_server_status,
)
def get_postgresql_server_status(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch server status metrics from a PostgreSQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_server_status,
    )
