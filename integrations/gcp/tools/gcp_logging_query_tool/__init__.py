"""Cloud Logging query tool — the CloudWatch Logs analogue for GCP."""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    LOGGING_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects, resource_name_batches
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_logging_query_tool.entries import normalize_entries
from integrations.gcp.tools.gcp_logging_query_tool.filters import build_filter

_COMPONENT = "integrations.gcp.tools.gcp_logging_query_tool"

_ANTI_EXAMPLES = (
    "Do not pass a timestamp clause in filter — the time window comes from hours.",
    "Do not guess a project id; omit project for the default, or call gcp_list_projects.",
    "Do not use for live Kubernetes pod logs when the cluster is registered and "
    "the pod still exists — kubernetes_get_pod_logs is faster and does not need "
    "Cloud Logging permissions. Do use it when the pod is gone, the cluster is "
    "not registered, or you need logs from before a restart.",
)


@tool(
    name="gcp_logging_query",
    display_name="Cloud Logging",
    source="gcp",
    description=(
        "Query Google Cloud Logging across one or more projects. Supply a Cloud "
        "Logging filter expression; the time window is set by hours, not by the "
        "filter. Set project to a comma-separated list, or '*' for every "
        "configured project, to correlate one request across projects in a "
        "single call."
    ),
    use_cases=[
        "Finding error entries for a service in the last hour",
        "Correlating a request id or trace across several GCP projects at once",
        "Reading Cloud Audit Log entries for a resource that changed before an incident",
        "Pulling GKE container logs when no kubeconfig is registered",
        "Locating which cluster and namespace a workload runs in — GKE entries "
        "carry cluster_name and namespace_name",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": (
                    "Cloud Logging filter, e.g. "
                    'resource.type="k8s_container" AND jsonPayload.error!=""'
                ),
            },
            "project": PROJECT_PROPERTY,
            "severity": {
                "type": "string",
                "description": (
                    "Minimum severity: DEBUG, INFO, NOTICE, WARNING, ERROR, "
                    "CRITICAL, ALERT, EMERGENCY"
                ),
            },
            "hours": {
                "type": "number",
                "description": "Lookback window in hours (default 1)",
                "default": 1,
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_logging_query(
    filter: str = "",  # noqa: A002 — matches the Cloud Logging API field name
    project: str = "",
    severity: str = "",
    hours: float = 1.0,
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch log entries from Cloud Logging."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, entries=[])

    log_filter = build_filter(user_filter=filter, severity=severity, hours=hours)
    page_size = max(1, min(int(limit), 1000))
    entries: list[dict[str, Any]] = []
    truncated = False

    # One client per credential, and one request per 100 projects under it — a
    # single-credential deployment of ordinary size is still exactly one call.
    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            service = build_service(config, LOGGING_API)
            for batch in resource_name_batches(group):
                response = (
                    service.entries()
                    .list(
                        body={
                            "resourceNames": batch,
                            "filter": log_filter,
                            "orderBy": "timestamp desc",
                            "pageSize": page_size,
                        }
                    )
                    .execute()
                )
                entries.extend(normalize_entries(response.get("entries") or []))
                # Cloud Logging returns a page token whenever more data exists.
                # Surfaced so the agent can say "these are the newest N", not
                # "this is all of it".
                truncated = truncated or bool(response.get("nextPageToken"))
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), entries=[])
        except Exception as exc:
            report_run_error(
                exc,
                tool_name="gcp_logging_query",
                source="gcp",
                component=_COMPONENT,
                method="logging.entries.list",
                extras={"projects": group, "filter": log_filter},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "filter": log_filter,
                "entries": [],
            }

    # Each credential returned its own newest-first page; interleave them so the
    # merged result is still newest-first, then re-apply the caller's cap.
    entries.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    if len(entries) > page_size:
        entries = entries[:page_size]
        truncated = True

    return {
        "found": bool(entries),
        "projects": projects,
        "filter": log_filter,
        "entry_count": len(entries),
        "entries": entries,
        "truncated": truncated,
    }
