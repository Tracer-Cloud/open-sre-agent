"""Cloud Run inventory — which serverless services exist and what is serving.

The serving tier for GCP estates that never adopted Kubernetes, and the place a
"my deploy did nothing" report resolves: Cloud Run keeps the previous revision
live when a new one fails to become ready, so the service stays healthy while
the change is invisible. ``locations/-`` covers every region in one call per
project, so the agent never has to guess a region.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    CLOUD_RUN_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_list_cloud_run_services_tool.services import normalize_services

_COMPONENT = "integrations.gcp.tools.gcp_list_cloud_run_services_tool"

_ANTI_EXAMPLES = (
    "Do not use this to read a Cloud Run service's logs or request rate — it "
    "reports configuration and rollout state. Use gcp_logging_query for logs "
    "and gcp_monitoring_query for request metrics.",
    "Do not pass a region; locations/- already covers every region in the project.",
)

_TRUNCATED_NOTE = (
    "More services exist than were returned. Narrow the result with name_contains, or raise limit."
)

_ROLLOUT_NOTE = (
    "One or more services are serving an older revision than the one last "
    "deployed. Check failing_conditions on those services: a revision that "
    "never became ready leaves the previous one taking all traffic, so a "
    "deploy can appear to succeed while changing nothing."
)


def _fetch(service: Any, project: str, page_size: int) -> dict[str, Any]:
    """Run one cross-region ``services.list`` call for a project."""
    payload: dict[str, Any] = (
        service.projects()
        .locations()
        .services()
        .list(parent=f"projects/{project}/locations/-", pageSize=page_size)
        .execute()
    )
    return payload


@tool(
    name="gcp_list_cloud_run_services",
    display_name="Cloud Run services",
    source="gcp",
    description=(
        "List Cloud Run services across the configured GCP projects, with "
        "readiness, the revision actually taking traffic, the container images "
        "it runs, the traffic split, scaling bounds and any failing condition. "
        "Covers every region in one call."
    ),
    use_cases=[
        "Checking whether a Cloud Run service is Ready or failing to start",
        "Finding which revision is serving traffic after a deploy",
        "Explaining why a deploy appeared to succeed but changed nothing",
        "Identifying the container image behind a failing service",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
            "name_contains": {
                "type": "string",
                "description": "Return only services whose name contains this substring.",
            },
            "unhealthy_only": {
                "type": "boolean",
                "description": (
                    "Return only services that are not Ready or whose latest "
                    "revision never took traffic."
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
def gcp_list_cloud_run_services(
    project: str = "",
    name_contains: str = "",
    unhealthy_only: bool = False,
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List Cloud Run services across the requested projects."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, services=[])

    needle = (name_contains or "").strip().lower()
    page_size = max(1, min(int(limit), 500))

    services: list[dict[str, Any]] = []
    unreachable: list[str] = []
    errors: list[str] = []
    truncated = False

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            client = build_service(config, CLOUD_RUN_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), services=[])
        except Exception as exc:
            # The client itself could not be constructed, so no project in this
            # group is reachable — there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_list_cloud_run_services",
                source="gcp",
                component=_COMPONENT,
                method="run.discovery.build",
                extras={"projects": group},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "services": [],
            }

        for target in group:
            try:
                response = _fetch(client, target, page_size)
            except Exception as exc:
                if api_not_enabled(exc):
                    # Cloud Run is off here, so the project runs no services —
                    # which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_list_cloud_run_services",
                    source="gcp",
                    component=_COMPONENT,
                    method="run.projects.locations.services.list",
                    severity="warning",
                    extras={"project": target},
                )
                # Per-project rather than fatal: Cloud Run is commonly enabled in
                # only some projects, and one 403 must not discard the others.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            raw = response.get("services")
            services.extend(normalize_services(raw if isinstance(raw, list) else [], target))
            truncated = truncated or bool(response.get("nextPageToken"))
            regions = response.get("unreachable")
            if isinstance(regions, list):
                unreachable.extend(str(region) for region in regions)

    if errors and not services:
        return {"found": False, "error": errors[0], "projects": projects, "services": []}

    if needle:
        services = [item for item in services if needle in str(item.get("name", "")).lower()]
    if unhealthy_only:
        services = [
            item for item in services if not item.get("ready") or item.get("rollout_pending")
        ]
    services.sort(key=lambda item: (str(item.get("project", "")), str(item.get("name", ""))))

    result: dict[str, Any] = {
        "found": bool(services),
        "projects": projects,
        "service_count": len(services),
        "services": services,
        "truncated": truncated,
    }
    notes = [note for note in (_TRUNCATED_NOTE if truncated else "",) if note]
    if any(item.get("rollout_pending") for item in services):
        notes.append(_ROLLOUT_NOTE)
    if notes:
        result["note"] = " ".join(notes)
    if unreachable:
        result["unreachable_regions"] = sorted(set(unreachable))
    if errors:
        result["partial_errors"] = errors
    return result
