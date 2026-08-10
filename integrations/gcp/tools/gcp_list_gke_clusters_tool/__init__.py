"""GKE cluster discovery — what clusters exist, and which ones kubectl can reach.

Closes a gap the Kubernetes integration cannot close on its own: that side only
knows about clusters an operator hand-registered as instances. This asks GCP,
so a cluster nobody registered still shows up during an investigation, and each
result says whether the Kubernetes tools can address it (``registered_as``) or
whether the agent is limited to Cloud Logging and Monitoring for it.

Listing is per project — ``container/v1`` scopes ``clusters.list`` by parent —
but the API client is built once per *credential*, so the common
one-credential-many-projects estate pays for a single auth round trip.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    CONTAINER_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from
from integrations.gcp.tools.gcp_list_gke_clusters_tool.clusters import normalize_clusters
from integrations.gcp.tools.gcp_list_gke_clusters_tool.correlation import annotate
from integrations.gcp.tools.gcp_list_gke_clusters_tool.params import gke_tool_params

_COMPONENT = "integrations.gcp.tools.gcp_list_gke_clusters_tool"

#: ``locations/-`` means "every region and zone", which is the only way to find
#: a cluster whose location the caller does not already know.
_ALL_LOCATIONS = "-"

_ANTI_EXAMPLES = (
    "Do not call this to read pod or workload state — it describes clusters, not "
    "what runs inside them. Use the kubernetes_* tools for that.",
    "Do not pass a cluster name as project; project takes a GCP project id.",
)

_UNREGISTERED_NOTE = (
    "Clusters listed in unregistered_clusters have no kubeconfig registered, so "
    "the kubernetes_* tools cannot reach them. Investigate those through "
    "gcp_logging_query and gcp_monitoring_query instead."
)


def _list_clusters(service: Any, project: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(clusters, missing locations)`` for one project.

    ``missingZones`` is GKE telling you the answer is incomplete — a zone it
    could not reach. Silently dropping it would let "no clusters found" mean
    "there is an outage in us-central1-a".
    """
    response: dict[str, Any] = (
        service.projects()
        .locations()
        .clusters()
        .list(parent=f"projects/{project}/locations/{_ALL_LOCATIONS}")
        .execute()
    )
    missing = response.get("missingZones")
    return (
        normalize_clusters(response.get("clusters") or [], project),
        [str(zone) for zone in missing] if isinstance(missing, list) else [],
    )


@tool(
    name="gcp_list_gke_clusters",
    display_name="GKE clusters",
    source="gcp",
    description=(
        "List Google Kubernetes Engine clusters across the configured GCP "
        "projects, with control-plane status, version, node counts and node "
        "pools. Each cluster reports whether a kubeconfig is registered for it "
        "(registered_as), which is the cluster name to pass to the kubernetes_* "
        "tools."
    ),
    use_cases=[
        "Finding which GKE clusters exist before deciding where an incident is happening",
        "Checking whether a cluster's control plane is DEGRADED or mid-upgrade",
        "Learning the cluster name to pass as the kubernetes_* tools' cluster argument",
        "Spotting a node pool that is unhealthy or has hit its autoscaling ceiling",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gke_tool_params,
)
def gcp_list_gke_clusters(
    project: str = "",
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    registered_clusters: list[dict[str, Any]] | None = None,
    # ``limit`` rides along on the shared GCP payload but does not apply: a
    # project holds a handful of clusters, not a page of log entries.
    **_injected: Any,
) -> dict[str, Any]:
    """List GKE clusters and correlate them with registered kubeconfigs."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, clusters=[])

    clusters: list[dict[str, Any]] = []
    missing_locations: list[str] = []
    errors: list[str] = []

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            service = build_service(config, CONTAINER_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), clusters=[])
        except Exception as exc:
            # Not a credential problem and not per-project either — the client
            # itself could not be constructed, so no project in this group is
            # reachable and there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_list_gke_clusters",
                source="gcp",
                component=_COMPONENT,
                method="container.discovery.build",
                extras={"projects": group},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "clusters": [],
            }

        for target in group:
            try:
                found, missing = _list_clusters(service, target)
            except Exception as exc:
                if api_not_enabled(exc):
                    # Kubernetes Engine is off here, so the project holds no
                    # clusters — which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_list_gke_clusters",
                    source="gcp",
                    component=_COMPONENT,
                    method="container.projects.locations.clusters.list",
                    severity="warning",
                    extras={"project": target},
                )
                # Per-project, not fatal: one project denying
                # container.clusters.list is routine in a shared estate and must
                # not discard the clusters the other projects returned.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            clusters.extend(found)
            missing_locations.extend(missing)

    if errors and not clusters:
        return {"found": False, "error": errors[0], "projects": projects, "clusters": []}

    unregistered = annotate(clusters, registered_clusters)

    result: dict[str, Any] = {
        "found": bool(clusters),
        "projects": projects,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }
    if unregistered:
        result["unregistered_clusters"] = unregistered
        result["note"] = _UNREGISTERED_NOTE
    if missing_locations:
        result["unreachable_locations"] = sorted(set(missing_locations))
    if errors:
        result["partial_errors"] = errors
    return result
