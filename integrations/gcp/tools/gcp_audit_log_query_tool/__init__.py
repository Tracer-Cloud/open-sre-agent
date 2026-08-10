"""Cloud Audit Logs tool — the CloudTrail analogue for GCP.

Answers the first question of most incident reviews: *what changed, and who
changed it?* Technically this is a Cloud Logging query, and ``gcp_logging_query``
could run the same one — but only if the caller already knows that audit entries
live under ``logName:cloudaudit.googleapis.com`` and that the interesting fields
hang off ``protoPayload``, not ``jsonPayload``. Both are easy to get wrong, and
getting them wrong returns zero entries with no hint of the mistake, which reads
to an agent as "nothing changed". A dedicated tool with named arguments removes
that failure mode.

Like the logging tool, one ``entries.list`` call covers every project reachable
by a single credential.
"""

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
from integrations.gcp.tools.gcp_audit_log_query_tool.filters import (
    LOG_TYPES,
    build_audit_filter,
    normalize_log_type,
)
from integrations.gcp.tools.gcp_audit_log_query_tool.records import normalize_records

_COMPONENT = "integrations.gcp.tools.gcp_audit_log_query_tool"

_ANTI_EXAMPLES = (
    "Do not pass a raw Cloud Logging filter — this tool builds the filter from "
    "its named arguments. Use gcp_logging_query when you need a custom filter.",
    "Do not use data_access to find configuration changes; admin writes are in "
    "the default activity log, and data_access is off unless someone enabled it.",
    "Do not pass a timestamp clause anywhere — the window comes from hours.",
)

_EMPTY_DATA_ACCESS_NOTE = (
    "Data Access audit logs are disabled by default in GCP, so an empty result "
    "here usually means the log type was never enabled rather than that nothing "
    "was read."
)


@tool(
    name="gcp_audit_log_query",
    display_name="Cloud Audit Logs",
    source="gcp",
    description=(
        "Find who changed what in Google Cloud, and when. Queries Cloud Audit "
        "Logs across one or more projects and returns one record per API call: "
        "principal, method, resource, source IP and whether it succeeded. Filter "
        "by principal, method, service or resource; set failed_only to see only "
        "denied or errored calls."
    ),
    use_cases=[
        "Identifying the change that preceded an incident (who deleted or resized what)",
        "Attributing a resource mutation to a user, service account or Terraform run",
        "Finding permission-denied calls and the exact IAM permission that was missing",
        "Auditing every action one principal took across projects during a window",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "log_type": {
                "type": "string",
                "description": (
                    "Audit stream: activity (admin writes, the default), "
                    "data_access, system_event, policy, or all."
                ),
                "enum": sorted(LOG_TYPES),
                "default": "activity",
            },
            "project": PROJECT_PROPERTY,
            "principal": {
                "type": "string",
                "description": (
                    "Match the acting identity, e.g. an email or service-account "
                    "name. Substring match."
                ),
            },
            "method": {
                "type": "string",
                "description": (
                    "Match the API method, e.g. compute.instances.delete or just "
                    "delete. Substring match."
                ),
            },
            "service": {
                "type": "string",
                "description": (
                    "Match the API service, e.g. compute.googleapis.com. Substring match."
                ),
            },
            "resource": {
                "type": "string",
                "description": (
                    "Match the target resource name, e.g. a cluster or instance "
                    "name. Substring match."
                ),
            },
            "failed_only": {
                "type": "boolean",
                "description": "Return only calls that were denied or errored.",
                "default": False,
            },
            "hours": {
                "type": "number",
                "description": "Lookback window in hours (default 24)",
                "default": 24,
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_audit_log_query(
    log_type: str = "activity",
    project: str = "",
    principal: str = "",
    method: str = "",
    service: str = "",
    resource: str = "",
    failed_only: bool = False,
    hours: float = 24.0,
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch Cloud Audit Log records describing changes to GCP resources."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, records=[])

    stream = normalize_log_type(log_type)
    audit_filter = build_audit_filter(
        log_type=stream,
        principal=principal,
        method=method,
        service=service,
        resource=resource,
        failed_only=failed_only,
        hours=hours,
    )
    page_size = max(1, min(int(limit), 1000))
    records: list[dict[str, Any]] = []
    truncated = False

    # One client per credential, one request per 100 projects under it: Cloud
    # Logging validates a request's resourceNames as a set, so an estate reached
    # via GCP_ADDITIONAL_PROJECTS=discover has to be split or none of it answers.
    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            client = build_service(config, LOGGING_API)
            for batch in resource_name_batches(group):
                response = (
                    client.entries()
                    .list(
                        body={
                            "resourceNames": batch,
                            "filter": audit_filter,
                            "orderBy": "timestamp desc",
                            "pageSize": page_size,
                        }
                    )
                    .execute()
                )
                records.extend(normalize_records(response.get("entries") or []))
                truncated = truncated or bool(response.get("nextPageToken"))
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), records=[])
        except Exception as exc:
            report_run_error(
                exc,
                tool_name="gcp_audit_log_query",
                source="gcp",
                component=_COMPONENT,
                method="logging.entries.list",
                extras={"projects": group, "log_type": stream},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "log_type": stream,
                "filter": audit_filter,
                "records": [],
            }

    # Each credential returned its own newest-first page; interleave so the
    # merged timeline is still newest-first, then re-apply the caller's cap.
    records.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    if len(records) > page_size:
        records = records[:page_size]
        truncated = True

    result: dict[str, Any] = {
        "found": bool(records),
        "projects": projects,
        "log_type": stream,
        "filter": audit_filter,
        "record_count": len(records),
        "records": records,
        "truncated": truncated,
    }
    if not records and stream == "data_access":
        result["note"] = _EMPTY_DATA_ACCESS_NOTE
    return result
