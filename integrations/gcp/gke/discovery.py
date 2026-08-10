"""List GKE clusters with the fields needed to *connect* to them.

A second projection of the same ``container/v1`` listing that backs the
``gcp_list_gke_clusters`` tool, and deliberately not shared with it. The tool
answers "what is wrong with this cluster" for a language model and drops the
endpoint and CA certificate, which are bulky and useless to a model. This
answers "how do I reach this cluster" for the registration flow and keeps
almost nothing else.

Error handling differs for the same reason: the tool degrades a per-project
failure into a ``partial_errors`` entry the model reads, while here the reader
is an operator at a terminal who is told which projects were skipped and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from integrations.gcp.client import (
    CONTAINER_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects
from integrations.gcp.tool_params import config_from
from integrations.gcp.tools.gcp_list_gke_clusters_tool.clusters import RUNNING, kubeconfig_context

_ALL_LOCATIONS = "-"


@dataclass(frozen=True)
class DiscoveredCluster:
    """One GKE cluster, reduced to what registration needs."""

    project: str
    name: str
    location: str
    endpoint: str
    ca_certificate: str
    status: str
    private_endpoint_only: bool

    @property
    def context(self) -> str:
        """The kubeconfig context name gcloud would write for this cluster."""
        return kubeconfig_context(self.project, self.location, self.name)

    @property
    def running(self) -> bool:
        """Whether the control plane is in a state that accepts connections."""
        return self.status == RUNNING


def _to_cluster(raw: dict[str, Any], project: str) -> DiscoveredCluster:
    private = raw.get("privateClusterConfig")
    private = private if isinstance(private, dict) else {}
    master_auth = raw.get("masterAuth")
    master_auth = master_auth if isinstance(master_auth, dict) else {}
    return DiscoveredCluster(
        project=project,
        name=str(raw.get("name", "")),
        # ``location`` is the modern field; ``zone`` survives on clusters
        # created before regional clusters existed.
        location=str(raw.get("location") or raw.get("zone") or ""),
        endpoint=str(raw.get("endpoint", "")),
        ca_certificate=str(master_auth.get("clusterCaCertificate", "")),
        status=str(raw.get("status", "")),
        private_endpoint_only=bool(private.get("enablePrivateEndpoint")),
    )


def _list_project(service: Any, project: str) -> list[DiscoveredCluster]:
    response: dict[str, Any] = (
        service.projects()
        .locations()
        .clusters()
        .list(parent=f"projects/{project}/locations/{_ALL_LOCATIONS}")
        .execute()
    )
    found = response.get("clusters")
    if not isinstance(found, list):
        return []
    return [_to_cluster(item, project) for item in found if isinstance(item, dict)]


@dataclass(frozen=True)
class Discovery:
    """What one sweep across a set of projects found.

    ``no_gke`` is kept apart from ``errors`` because the two want different
    reporting: a project with the Kubernetes Engine API switched off simply has
    no clusters, and listing every one of them as a failure is what buries the
    projects that genuinely could not be read.
    """

    clusters: list[DiscoveredCluster]
    errors: list[str]
    no_gke: list[str]


def discover_clusters(
    projects: list[str],
    project_configs: dict[str, dict[str, Any]] | None,
) -> Discovery:
    """Discover the reachable GKE clusters across ``projects``.

    One API client per credential, one request per project — the same shape as
    the tools, so a single-credential estate authenticates once. A project that
    denies ``container.clusters.list`` contributes an error string and is
    skipped; it never aborts the projects that would have succeeded. A project
    with no Kubernetes Engine API is recorded in ``no_gke`` instead, since it
    cannot hold a cluster and so has nothing to report.
    """
    clusters: list[DiscoveredCluster] = []
    errors: list[str] = []
    no_gke: list[str] = []

    for config_payload, group in group_projects(projects, project_configs):
        try:
            service = build_service(
                config_from(config_payload, fallback_project=group[0]), CONTAINER_API
            )
        except GCPClientError as exc:
            errors.append(f"{', '.join(group)}: {exc}")
            continue
        for project in group:
            try:
                clusters.extend(_list_project(service, project))
            except Exception as exc:
                if api_not_enabled(exc):
                    no_gke.append(project)
                    continue
                errors.append(f"{project}: {describe_api_error(exc)}")
    return Discovery(clusters=clusters, errors=errors, no_gke=no_gke)
