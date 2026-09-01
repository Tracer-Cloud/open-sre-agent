"""Logs a managed database keeps for itself, outside Cloud Logging.

A managed cluster writes its own log stream and serves it from its own service
endpoint. Nothing of it reaches Cloud Logging unless the operator turned that
export on, so ``read_yc_logs`` coming back empty says only "not in Cloud
Logging" — which reads exactly like "there are no logs" and ends investigations
early.

Each engine keeps several streams and the endpoint serves one at a time. The
choice matters more than it looks: MySQL answers with its error log by default,
so a question about slow queries gets a confident empty answer unless
``MYSQL_SLOW_QUERY`` is asked for. ClickHouse refuses the read outright without
one.
"""

from __future__ import annotations

from typing import Any, Final

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
from integrations.yandex_cloud.mdb_catalog import engine_choices, resolve_engine

SOURCE = "yandex_cloud"


#: See the note beside the same constant in yc_db_tool: one sentence rather than
#: the full SKILL.md slice, because duplicating 2400 characters per tool buys
#: nothing an investigation actually needs.
_READ_ONLY_HANDOFF: Final = "These tools only read, so when the finding calls for a change, end with the exact `yc ...` command an operator can paste."

#: Enough to see a pattern without flooding the prompt.
DEFAULT_PAGE_SIZE = 100

# Kept out of the list literal below: adjacent strings inside a list display are
# indistinguishable from a missing comma.
_CLOUD_LOGGING_ANTI_EXAMPLE = (
    "Reading application logs your own workload writes. Those go to Cloud "
    "Logging and are read with read_yc_logs. This endpoint serves only what the "
    "database engine itself writes."
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _map_db_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite which stream was read and how much of it came back.

    The stream is named because reading the wrong one is the trap this tool
    exists for: MySQL's error log answering a question about slow queries looks
    exactly like an answer.
    """
    if not output.get("available"):
        return
    entries = output.get("logs") or []
    if not entries:
        return
    stream = output.get("service_type") or "default"
    record_evidence_entry(
        evidence,
        source="yc_db_logs",
        label="Yandex Managed Database Logs",
        summary=f"{len(entries)} entries from the {stream} stream",
    )


@tool(
    name="read_yc_db_logs",
    evidence_mapper=_map_db_logs,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.ACTION),
    display_name="Managed Databases",
    source=SOURCE,
    description=(
        "Read the log a managed database cluster keeps for itself — PostgreSQL, "
        "MySQL, ClickHouse, Valkey, StoreDoc, Kafka, OpenSearch or MPP "
        "Analytics. This is a separate store from Cloud Logging: a cluster's own "
        "log is here and nowhere else unless export was switched on, so an empty "
        "read_yc_logs is not evidence a cluster logged nothing. Each engine keeps "
        "several streams and one is served at a time, so name the stream when the "
        "question is about slow queries or the pooler rather than general errors. "
        + _READ_ONLY_HANDOFF
    ),
    use_cases=[
        "Finding why a managed database is erroring or restarting",
        "Reading slow-query logs when a database is the suspected bottleneck",
        "Checking pooler logs when connections are being refused",
        "Reading a cluster's own log after Cloud Logging came back empty",
    ],
    anti_examples=[_CLOUD_LOGGING_ANTI_EXAMPLE],
    requires=["engine", "cluster_id"],
    outputs={
        "logs": "log entries, newest last",
        "service_type": "which stream was read",
        "available_service_types": "the other streams this engine keeps",
        "count": "how many entries were returned",
        "next_page_token": "token for the following page, when there is one",
    },
    input_schema={
        "type": "object",
        "properties": {
            "engine": {
                "type": "string",
                "description": "Database engine, e.g. postgresql, mysql, clickhouse.",
            },
            "cluster_id": {
                "type": "string",
                "description": "Cluster id, from list_yc_db_clusters.",
            },
            "service_type": {
                "type": "string",
                "description": (
                    "Which log stream to read. Defaults to the engine's primary one, "
                    "which for MySQL is the error log rather than the slow-query log. "
                    "The result lists the alternatives."
                ),
                "default": "",
            },
            "filter": {
                "type": "string",
                "description": (
                    "Server-side filter, e.g. message.hostname='rc1a-abc.mdb.yandexcloud.net'."
                ),
                "default": "",
            },
            "from_time": {
                "type": "string",
                "description": "RFC3339 start of the window. Required for a past incident.",
                "default": "",
            },
            "to_time": {
                "type": "string",
                "description": "RFC3339 end of the window.",
                "default": "",
            },
            "page_size": {
                "type": "integer",
                "description": "Entries per page.",
                "default": DEFAULT_PAGE_SIZE,
            },
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            },
        },
        "required": ["engine", "cluster_id"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def read_yc_db_logs(
    engine: str,
    cluster_id: str,
    service_type: str = "",
    filter: str = "",  # noqa: A002 - the API calls it this, and so should the schema
    from_time: str = "",
    to_time: str = "",
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read a managed database cluster's own log."""
    resolved = resolve_engine(engine)
    if resolved is None:
        return {
            "source": SOURCE,
            "available": False,
            "error": f"Unknown engine '{engine}'. Use one of: {engine_choices()}.",
        }

    streams = list(resolved.log_service_types)
    chosen = service_type.strip().upper() or (streams[0] if streams else "")
    if streams and chosen not in streams:
        return {
            "source": SOURCE,
            "available": False,
            "error": (
                f"{resolved.label} has no log stream '{chosen}'. Available: {', '.join(streams)}."
            ),
            "available_service_types": streams,
        }

    if yc_backend is not None:
        response = dict(yc_backend.read_yc_db_logs(resolved.key, cluster_id, chosen))
    else:
        client = client_from_params(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

        params: dict[str, Any] = {}
        if chosen:
            params["serviceType"] = chosen
        if filter:
            params["filter"] = filter
        if from_time:
            params["fromTime"] = from_time
        if to_time:
            params["toTime"] = to_time

        response = client.get(
            resolved.service,
            f"{resolved.path}/clusters/{cluster_id}:logs",
            params,
            page_token=page_token,
            page_size=page_size,
        )

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "engine": resolved.key,
            "cluster_id": cluster_id,
            "service_type": chosen,
            "available_service_types": streams,
            "error": response.get("error", "Could not read the cluster log."),
        }

    entries = (response.get("data") or {}).get("logs") or []
    return {
        "source": SOURCE,
        "available": True,
        "engine": resolved.key,
        "cluster_id": cluster_id,
        "service_type": chosen,
        "available_service_types": [stream for stream in streams if stream != chosen],
        "logs": entries,
        "count": len(entries),
        "next_page_token": (response.get("metadata") or {}).get("next_page_token", ""),
    }


__all__ = ["read_yc_db_logs"]
