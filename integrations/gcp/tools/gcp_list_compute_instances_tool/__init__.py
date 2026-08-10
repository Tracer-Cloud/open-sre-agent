"""Compute Engine inventory — which VMs exist and what state they are in.

The infrastructure layer under everything else: a GKE node that vanished, a
stopped VM behind a failing health check, or the machine type that explains a
memory ceiling. ``aggregatedList`` covers every zone in one call per project,
so the agent never has to guess a zone.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    COMPUTE_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_list_compute_instances_tool.instances import flatten_aggregated

_COMPONENT = "integrations.gcp.tools.gcp_list_compute_instances_tool"

#: Compute states, for the ``status`` argument's enum. TERMINATED means
#: "stopped", not "deleted" — a deleted instance is simply absent.
INSTANCE_STATES: tuple[str, ...] = (
    "PROVISIONING",
    "STAGING",
    "RUNNING",
    "STOPPING",
    "SUSPENDING",
    "SUSPENDED",
    "REPAIRING",
    "TERMINATED",
)

_ANTI_EXAMPLES = (
    "Do not use this to read what runs inside a GKE node — it reports the VM, "
    "not its pods. Use the kubernetes_* tools for workloads.",
    "Do not pass a zone; aggregatedList already covers every zone in the project.",
)

_TRUNCATED_NOTE = (
    "More instances exist than were returned. Narrow the result with "
    "name_contains or status, or raise limit."
)


def _fetch(service: Any, project: str, page_size: int, api_filter: str) -> dict[str, Any]:
    """Run one ``instances.aggregatedList`` call for a project."""
    payload: dict[str, Any] = (
        service.instances()
        .aggregatedList(project=project, maxResults=page_size, filter=api_filter or None)
        .execute()
    )
    return payload


@tool(
    name="gcp_list_compute_instances",
    display_name="Compute Engine VMs",
    source="gcp",
    description=(
        "List Compute Engine virtual machines across the configured GCP "
        "projects, with status, machine type, zone, IP addresses, labels and "
        "network tags. Covers every zone in one call. GKE nodes report the "
        "cluster and node pool they belong to."
    ),
    use_cases=[
        "Checking whether a VM is RUNNING, TERMINATED or stuck in REPAIRING",
        "Finding the internal IP or machine type of a host named in an alert",
        "Identifying which GKE cluster and node pool a node VM belongs to",
        "Spotting preemptible VMs whose disappearance is expected, not an incident",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
            "status": {
                "type": "string",
                "description": "Return only instances in this state, e.g. RUNNING.",
                "enum": list(INSTANCE_STATES),
            },
            "name_contains": {
                "type": "string",
                "description": "Return only instances whose name contains this substring.",
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_list_compute_instances(
    project: str = "",
    status: str = "",
    name_contains: str = "",
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List Compute Engine instances across the requested projects."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, instances=[])

    wanted_state = (status or "").strip().upper()
    if wanted_state and wanted_state not in INSTANCE_STATES:
        return tool_unavailable(
            "gcp",
            f"unknown status '{status}'; valid values: {', '.join(INSTANCE_STATES)}",
            instances=[],
        )
    # Status filters server-side, which is what keeps a large estate's response
    # small. Name matching stays client-side: the Compute filter language uses a
    # regex dialect of its own, and a caller-supplied substring is not a regex.
    api_filter = f'status = "{wanted_state}"' if wanted_state else ""
    needle = (name_contains or "").strip().lower()
    page_size = max(1, min(int(limit), 500))

    instances: list[dict[str, Any]] = []
    unreachable: list[str] = []
    errors: list[str] = []
    truncated = False

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            service = build_service(config, COMPUTE_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), instances=[])
        except Exception as exc:
            # The client itself could not be constructed, so no project in this
            # group is reachable — there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_list_compute_instances",
                source="gcp",
                component=_COMPONENT,
                method="compute.discovery.build",
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
                response = _fetch(service, target, page_size, api_filter)
            except Exception as exc:
                if api_not_enabled(exc):
                    # Compute is off here, so the project runs no instances —
                    # which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_list_compute_instances",
                    source="gcp",
                    component=_COMPONENT,
                    method="compute.instances.aggregatedList",
                    severity="warning",
                    extras={"project": target, "filter": api_filter},
                )
                # Per-project rather than fatal, for the same reason as the GKE
                # listing: one denied project must not discard the others.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            instances.extend(flatten_aggregated(response.get("items"), target))
            truncated = truncated or bool(response.get("nextPageToken"))
            scopes = response.get("unreachables")
            if isinstance(scopes, list):
                unreachable.extend(str(scope) for scope in scopes)

    if errors and not instances:
        return {"found": False, "error": errors[0], "projects": projects, "instances": []}

    if needle:
        instances = [item for item in instances if needle in str(item.get("name", "")).lower()]
    instances.sort(key=lambda item: (str(item.get("project", "")), str(item.get("name", ""))))

    result: dict[str, Any] = {
        "found": bool(instances),
        "projects": projects,
        "instance_count": len(instances),
        "instances": instances,
        "truncated": truncated,
    }
    if truncated:
        result["note"] = _TRUNCATED_NOTE
    if unreachable:
        result["unreachable_scopes"] = sorted(set(unreachable))
    if errors:
        result["partial_errors"] = errors
    return result
