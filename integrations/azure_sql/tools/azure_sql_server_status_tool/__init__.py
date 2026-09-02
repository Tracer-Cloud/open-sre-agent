"""Azure SQL Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.azure_sql import (
    azure_sql_extract_params,
    azure_sql_is_available,
    get_server_status,
    resolve_azure_sql_config,
)


def _map_get_azure_sql_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite connection counts, service tier, and resource saturation."""
    if not output.get("available"):
        return
    tier = output.get("service_tier") or {}
    connections = output.get("connections") or {}
    resources = output.get("resource_utilization") or {}
    parts = []
    total_conns = connections.get("total")
    if isinstance(total_conns, int):
        parts.append(f"{total_conns} connection(s)")
    if isinstance(connections.get("active"), int):
        parts.append(f"{connections.get('active')} active")
    if isinstance(resources.get("avg_cpu_percent"), (int, float)):
        parts.append(f"CPU {resources.get('avg_cpu_percent')}%")
    if isinstance(resources.get("avg_memory_usage_percent"), (int, float)):
        parts.append(f"memory {resources.get('avg_memory_usage_percent')}%")
    tier_label = tier.get("service_objective") or tier.get("edition")
    if tier_label:
        parts.append(f"tier {tier_label}")
    if not parts:
        return
    record_evidence_entry(
        evidence,
        source="get_azure_sql_server_status",
        label="Azure SQL Server Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_azure_sql_server_status",
    description="Retrieve Azure SQL Database server metrics including service tier, resource utilization, connections, and database size.",
    source="azure_sql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Checking Azure SQL Database health during an incident",
        "Identifying DTU/vCore throttling or resource exhaustion",
        "Reviewing service tier and connection saturation",
    ],
    is_available=azure_sql_is_available,
    injected_params=("server",),
    extract_params=azure_sql_extract_params,
    evidence_mapper=_map_get_azure_sql_server_status,
)
def get_azure_sql_server_status(
    server: str,
    database: str | None = None,
    port: int = 1433,
) -> dict[str, Any]:
    """Fetch server status metrics from an Azure SQL Database instance."""
    _db_defaulted = database is None
    if database is None:
        database = "master"
    config = resolve_azure_sql_config(server=server, database=database, port=port)
    result = get_server_status(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'master'. Results may not reflect application data."
        )
    return result
