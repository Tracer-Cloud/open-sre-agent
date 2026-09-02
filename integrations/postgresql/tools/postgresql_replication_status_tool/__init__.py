"""PostgreSQL Replication Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_replication_status,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


def _map_get_postgresql_replication_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite primary/replica role and streaming replica count."""
    if not output.get("available"):
        return
    if not output.get("is_primary", False):
        record_evidence_entry(
            evidence,
            source="get_postgresql_replication_status",
            label="PostgreSQL Replication Status",
            summary="server is a replica, not a primary",
        )
        return
    replicas = output.get("replicas") or []
    count = output.get("replica_count")
    if not isinstance(count, int) or count <= 0:
        count = len(replicas) if isinstance(replicas, list) else 0
    if count <= 0:
        return
    parts = [f"{count} streaming replica(s)"]
    record_evidence_entry(
        evidence,
        source="get_postgresql_replication_status",
        label="PostgreSQL Replication Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_postgresql_replication_status",
    description="Retrieve PostgreSQL replication status including replica lag, WAL positions, and streaming status.",
    source="postgresql",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    use_cases=[
        "Investigating replication lag issues during database incidents",
        "Checking replica health and synchronization status",
        "Monitoring WAL streaming and replica connectivity problems",
    ],
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
    evidence_mapper=_map_get_postgresql_replication_status,
)
def get_postgresql_replication_status(
    host: str,
    database: str | None = None,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch replication status from a PostgreSQL primary server."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_replication_status,
    )
