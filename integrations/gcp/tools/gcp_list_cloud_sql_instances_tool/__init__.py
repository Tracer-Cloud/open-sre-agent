"""Cloud SQL inventory — the managed database under a failing application.

The tier an application team usually cannot see. An instance that is SUSPENDED
for a billing issue, mid-failover, in maintenance, or out of disk presents to
the caller as connection errors and timeouts, which read as an application
fault until someone looks here. ``instances.list`` is one call per project and
covers every region.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    CLOUD_SQL_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_list_cloud_sql_instances_tool.instances import (
    RUNNABLE,
    normalize_instances,
)

_COMPONENT = "integrations.gcp.tools.gcp_list_cloud_sql_instances_tool"

#: Cloud SQL instance states, for the ``state`` argument's enum.
INSTANCE_STATES: tuple[str, ...] = (
    "RUNNABLE",
    "SUSPENDED",
    "PENDING_CREATE",
    "PENDING_DELETE",
    "MAINTENANCE",
    "ONLINE_MAINTENANCE",
    "FAILED",
    "REPAIRING",
)

_ANTI_EXAMPLES = (
    "Do not use this to read slow queries or connection counts — it reports the "
    "instance, not its workload. Use gcp_monitoring_query for database metrics "
    "and gcp_logging_query for Cloud SQL logs.",
    "Do not pass a region; instances.list already covers every region in the project.",
)

_TRUNCATED_NOTE = (
    "More instances exist than were returned. Narrow the result with "
    "name_contains or state, or raise limit."
)

_DISK_NOTE = (
    "One or more instances are near their provisioned disk. Cloud SQL stops "
    "accepting writes when the disk fills and still reports the instance "
    "RUNNABLE, so this is worth ruling in before blaming the application."
)


def _fetch(service: Any, project: str, page_size: int, api_filter: str) -> dict[str, Any]:
    """Run one ``instances.list`` call for a project."""
    payload: dict[str, Any] = (
        service.instances()
        .list(project=project, maxResults=page_size, filter=api_filter or None)
        .execute()
    )
    return payload


@tool(
    name="gcp_list_cloud_sql_instances",
    display_name="Cloud SQL instances",
    source="gcp",
    description=(
        "List Cloud SQL database instances across the configured GCP projects, "
        "with state, database version, tier, high-availability type, disk "
        "usage, replica topology, scheduled maintenance and suspension "
        "reasons. Covers every region in one call."
    ),
    use_cases=[
        "Checking whether a database instance is RUNNABLE or SUSPENDED",
        "Finding an instance that is close to filling its disk and will refuse writes",
        "Seeing whether an instance has a standby (REGIONAL) or is single-zone",
        "Identifying which primary a read replica follows when writes fail",
        "Spotting a maintenance window that lines up with the start of an incident",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
            "state": {
                "type": "string",
                "description": "Return only instances in this state, e.g. SUSPENDED.",
                "enum": list(INSTANCE_STATES),
            },
            "name_contains": {
                "type": "string",
                "description": "Return only instances whose name contains this substring.",
            },
            "unhealthy_only": {
                "type": "boolean",
                "description": (
                    "Return only instances that are not RUNNABLE or are near their disk limit."
                ),
                "default": False,
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_list_cloud_sql_instances(
    project: str = "",
    state: str = "",
    name_contains: str = "",
    unhealthy_only: bool = False,
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List Cloud SQL instances across the requested projects."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, instances=[])

    wanted_state = (state or "").strip().upper()
    if wanted_state and wanted_state not in INSTANCE_STATES:
        return tool_unavailable(
            "gcp",
            f"unknown state '{state}'; valid values: {', '.join(INSTANCE_STATES)}",
            instances=[],
        )
    # State filters server-side. Name matching stays client-side because the
    # Cloud SQL filter language has no substring operator, only equality.
    api_filter = f"state:{wanted_state}" if wanted_state else ""
    needle = (name_contains or "").strip().lower()
    page_size = max(1, min(int(limit), 500))

    instances: list[dict[str, Any]] = []
    unreachable: list[str] = []
    errors: list[str] = []
    truncated = False

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            client = build_service(config, CLOUD_SQL_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), instances=[])
        except Exception as exc:
            # The client itself could not be constructed, so no project in this
            # group is reachable — there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_list_cloud_sql_instances",
                source="gcp",
                component=_COMPONENT,
                method="sqladmin.discovery.build",
                extras={"projects": group},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "instances": [],
            }

        for target in group:
            try:
                response = _fetch(client, target, page_size, api_filter)
            except Exception as exc:
                if api_not_enabled(exc):
                    # The Admin API is off here, so the project holds no
                    # instances — which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_list_cloud_sql_instances",
                    source="gcp",
                    component=_COMPONENT,
                    method="sqladmin.instances.list",
                    severity="warning",
                    extras={"project": target, "filter": api_filter},
                )
                # Per-project rather than fatal: the Cloud SQL Admin API is
                # commonly disabled in projects that run no database.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            raw = response.get("items")
            instances.extend(normalize_instances(raw if isinstance(raw, list) else [], target))
            truncated = truncated or bool(response.get("nextPageToken"))
            regions = response.get("warnings")
            if isinstance(regions, list):
                unreachable.extend(
                    str(entry.get("message", "")) for entry in regions if isinstance(entry, dict)
                )

    if errors and not instances:
        return {"found": False, "error": errors[0], "projects": projects, "instances": []}

    if needle:
        instances = [item for item in instances if needle in str(item.get("name", "")).lower()]
    if unhealthy_only:
        instances = [item for item in instances if not item.get("healthy")]
    instances.sort(key=lambda item: (str(item.get("project", "")), str(item.get("name", ""))))

    result: dict[str, Any] = {
        "found": bool(instances),
        "projects": projects,
        "instance_count": len(instances),
        "instances": instances,
        "truncated": truncated,
    }
    notes = [note for note in (_TRUNCATED_NOTE if truncated else "",) if note]
    if any(item.get("disk_pressure") for item in instances):
        notes.append(_DISK_NOTE)
    if notes:
        result["note"] = " ".join(notes)
    if any(item.get("state") != RUNNABLE for item in instances):
        result["unhealthy_count"] = sum(1 for item in instances if item.get("state") != RUNNABLE)
    if unreachable:
        result["warnings"] = sorted({entry for entry in unreachable if entry})
    if errors:
        result["partial_errors"] = errors
    return result
