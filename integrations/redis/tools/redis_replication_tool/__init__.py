"""Redis Replication Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_replication,
    redis_extract_params,
    redis_is_available,
)


def _map_get_redis_replication(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    role = output.get("role", "")
    if not role:
        return
    replicas = output.get("replicas", [])
    connected_slaves = output.get("connected_slaves", 0)
    replica_count = len(replicas) if replicas else connected_slaves
    summary = f"Role: {role}, {replica_count} replica(s) connected"
    record_evidence_entry(
        evidence,
        source="get_redis_replication",
        label="Redis Replication Status",
        summary=summary,
    )


@tool(
    name="get_redis_replication",
    description=(
        "Retrieve Redis replication status: node role, master link health, "
        "connected replicas, and per-replica offset lag."
    ),
    source="redis",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    evidence_mapper=_map_get_redis_replication,
    use_cases=[
        "Check replication health when investigating stale reads or a failover event.",
        "Measure replica offset lag and master link status across connected replicas.",
    ],
    outputs={
        "role": "Node role: master or slave.",
        "connected_slaves": "Number of replicas connected to a master.",
        "master": "For replicas: master host/port, link status, and sync progress.",
        "replicas": "For masters: per-replica address, state, offset, and lag_bytes.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_replication(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Fetch replication status and replica lag from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_replication(config)
