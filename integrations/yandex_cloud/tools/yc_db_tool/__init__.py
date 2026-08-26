"""Managed database cluster health, hosts, and recent operations."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.mdb_engines import Engine, engine_choices, resolve_engine
from integrations.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yandex_cloud"

#: The private CA managed clusters present, which is in no system trust store.
CA_CERTIFICATE_URL = "https://storage.yandexcloud.net/cloud-certs/CA.pem"

_HEALTHY = "ALIVE"
_RUNNING = "RUNNING"
_RECENT_OPERATIONS = 10


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _summarize_cluster(cluster: dict[str, Any], engine: Engine) -> dict[str, Any]:
    return {
        "id": cluster.get("id", ""),
        "name": cluster.get("name", ""),
        "engine": engine.key,
        "status": cluster.get("status", ""),
        "health": cluster.get("health", ""),
        "environment": cluster.get("environment", ""),
        "created_at": cluster.get("createdAt", ""),
        "healthy": cluster.get("status") == _RUNNING and cluster.get("health") == _HEALTHY,
    }


def _summarize_host(host: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": host.get("name", ""),
        "zone": host.get("zoneId", ""),
        "role": host.get("role", ""),
        "health": host.get("health", ""),
        "type": host.get("type", ""),
        "public": bool(host.get("assignPublicIp", False)),
        "replica_of": host.get("replicaSource", ""),
    }


def _summarize_operation(operation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": operation.get("id", ""),
        "description": operation.get("description", ""),
        "created_at": operation.get("createdAt", ""),
        "done": operation.get("done", False),
        "error": (operation.get("error") or {}).get("message", ""),
    }


def _connection_hint(engine: Engine, hosts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return how to reach the data plane, which is where the real answers are."""
    primary = next(
        (host for host in hosts if str(host.get("role", "")).upper() in {"MASTER", "PRIMARY"}),
        hosts[0] if hosts else None,
    )
    return {
        "integration": engine.integration,
        "host": primary["name"] if primary else "",
        "port": engine.port,
        "tls": (
            "Public hosts require TLS against Yandex's private CA, which is in no "
            f"system trust store. Fetch it from {CA_CERTIFICATE_URL}."
        ),
    }


def _list_clusters(client: YandexCloudClient, engine: Engine, page_token: str) -> dict[str, Any]:
    return client.get(
        engine.service,
        f"{engine.path}/clusters",
        {"folderId": client.folder_id},
        page_token=page_token,
    )


def _map_db_clusters(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite how many clusters were found and how many are not healthy."""
    if not output.get("available"):
        return
    clusters = output.get("clusters") or []
    if not clusters:
        return
    unhealthy = output.get("unhealthy") or []
    # "0 unhealthy" is a finding, not noise: it is what rules the database out.
    summary = f"{len(clusters)} managed database cluster(s), {len(unhealthy)} unhealthy"
    record_evidence_entry(
        evidence,
        source="yc_db_clusters",
        label="Yandex Managed Databases",
        summary=summary,
    )


def _map_db_cluster(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the cluster's health, its unhealthy hosts, and its latest operation."""
    if not output.get("available"):
        return
    cluster = output.get("cluster") or {}
    if not cluster:
        return
    hosts = output.get("hosts") or []
    unhealthy = output.get("unhealthy_hosts") or []
    parts = [
        f"'{cluster.get('name', cluster.get('id', 'unknown'))}'",
        f"{cluster.get('status', 'unknown')}/{cluster.get('health', 'unknown')}",
        f"{len(hosts)} host(s), {len(unhealthy)} unhealthy",
    ]
    # The most recent operation is what a failover or a resize looks like from
    # here, and it is usually the answer to "what changed".
    operations = output.get("recent_operations") or []
    if operations:
        latest = operations[0].get("description") or ""
        if latest:
            parts.append(f"latest operation: {latest}")
    record_evidence_entry(
        evidence,
        source="yc_db_cluster",
        label="Yandex Managed Database Cluster",
        summary=", ".join(parts),
    )


@tool(
    name="list_yc_db_clusters",
    evidence_mapper=_map_db_clusters,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.ACTION),
    display_name="Managed Databases",
    source=SOURCE,
    description=(
        "List managed database clusters in the folder with their status and "
        "health. Covers PostgreSQL, MySQL, ClickHouse, Valkey (was Redis), "
        "StoreDoc (was MongoDB), Kafka, OpenSearch, and MPP Analytics (was "
        "Greenplum). Omit the engine to search all of them."
    ),
    use_cases=[
        "Finding a cluster id from its name",
        "Checking whether a database cluster is degraded before blaming the application",
        "Establishing what databases exist in a folder",
    ],
    requires=[],
    outputs={
        "clusters": "clusters with engine, status, and health",
        "unhealthy": "the subset that is not running and alive",
        "count": "how many clusters were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "engine": {
                "type": "string",
                "description": (
                    "Which engine to list. Omit to search every engine. "
                    "Former names (redis, mongodb, greenplum) are accepted."
                ),
                "default": "",
            }
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_db_clusters(
    engine: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List managed database clusters."""
    if yc_backend is not None:
        return dict(yc_backend.list_yc_db_clusters(engine))

    from integrations.yandex_cloud.mdb_engines import ENGINES

    engines: tuple[Engine, ...] = ENGINES
    if engine.strip():
        resolved = resolve_engine(engine)
        if resolved is None:
            return {
                "source": SOURCE,
                "available": False,
                "error": f"Unknown engine '{engine}'. Use one of: {engine_choices()}.",
            }
        engines = (resolved,)

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    clusters: list[dict[str, Any]] = []
    errors: list[str] = []
    answered = 0
    for candidate in engines:
        response = _list_clusters(client, candidate, "")
        if not response.get("success"):
            # One engine being unavailable should not hide the others; a folder
            # rarely uses every engine and permissions are often per-service.
            errors.append(f"{candidate.key}: {response.get('error', '')}")
            continue
        answered += 1
        raw = (response.get("data") or {}).get("clusters") or []
        clusters.extend(_summarize_cluster(cluster, candidate) for cluster in raw)

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "clusters": clusters,
        "unhealthy": [cluster for cluster in clusters if not cluster["healthy"]],
        "count": len(clusters),
    }
    # An engine answering with nothing is an answer: most folders run one or two
    # engines, so "no clusters" is the common truthful result. Only report the
    # tool as unavailable when no engine could be reached at all.
    if not answered:
        result["available"] = False
        result["error"] = "; ".join(errors)
    elif errors:
        result["note"] = "Some engines could not be listed: " + "; ".join(errors)
    return result


@tool(
    name="get_yc_db_cluster",
    evidence_mapper=_map_db_cluster,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.ACTION),
    display_name="Managed Databases",
    source=SOURCE,
    description=(
        "Read one managed database cluster: its health, every host with its "
        "role and zone, and recent operations. Recent operations are where a "
        "failover, a restart, or a resize shows up — often the thing that "
        "explains an incident. Also returns how to connect the matching "
        "data-plane integration for querying the database itself."
    ),
    use_cases=[
        "Checking whether a failover happened around the time of an incident",
        "Finding which host is currently the master after a role change",
        "Spotting a single unhealthy replica in an otherwise healthy cluster",
        "Getting the host and port to point the postgresql or clickhouse integration at",
    ],
    requires=["cluster_id", "engine"],
    outputs={
        "cluster": "status, health, and environment",
        "hosts": "each host with role, zone, and health",
        "recent_operations": "the most recent operations, newest first",
        "connect": "which integration, host, and port reach the data plane",
    },
    input_schema={
        "type": "object",
        "properties": {
            "cluster_id": {
                "type": "string",
                "description": "Cluster id, as returned by list_yc_db_clusters.",
            },
            "engine": {
                "type": "string",
                "description": (
                    "Which engine the cluster runs. Former names "
                    "(redis, mongodb, greenplum) are accepted."
                ),
            },
        },
        "required": ["cluster_id", "engine"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def get_yc_db_cluster(
    cluster_id: str,
    engine: str,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read one managed database cluster with its hosts and recent operations."""
    if not cluster_id.strip():
        return tool_unavailable(
            SOURCE, "cluster_id is required. Call list_yc_db_clusters to find one."
        )

    resolved = resolve_engine(engine)
    if resolved is None:
        return {
            "source": SOURCE,
            "available": False,
            "error": f"Unknown engine '{engine}'. Use one of: {engine_choices()}.",
        }

    if yc_backend is not None:
        return dict(yc_backend.get_yc_db_cluster(cluster_id, resolved.key))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    detail = client.get(resolved.service, f"{resolved.path}/clusters/{cluster_id}", page_size=None)
    if not detail.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": detail.get("error", "Could not read the cluster."),
            "cluster_id": cluster_id,
        }

    hosts_response = client.get(resolved.service, f"{resolved.path}/clusters/{cluster_id}/hosts")
    raw_hosts = (hosts_response.get("data") or {}).get("hosts") or []
    hosts = [_summarize_host(host) for host in raw_hosts]

    operations_response = client.get(
        resolved.service, f"{resolved.path}/clusters/{cluster_id}/operations"
    )
    raw_operations = (operations_response.get("data") or {}).get("operations") or []
    operations = [_summarize_operation(op) for op in raw_operations[:_RECENT_OPERATIONS]]

    return {
        "source": SOURCE,
        "available": True,
        "cluster_id": cluster_id,
        "engine": resolved.key,
        "cluster": _summarize_cluster(detail.get("data") or {}, resolved),
        "hosts": hosts,
        "unhealthy_hosts": [host for host in hosts if host["health"] not in {_HEALTHY, ""}],
        "recent_operations": operations,
        "connect": _connection_hint(resolved, raw_hosts),
    }


__all__ = ["get_yc_db_cluster", "list_yc_db_clusters"]
