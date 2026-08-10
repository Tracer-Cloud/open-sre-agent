"""GKE cluster auto-registration.

Turns a cluster discovered through the GCP API into a named Kubernetes instance
the ``kubernetes_*`` tools can address, closing the gap ``gcp_list_gke_clusters``
reports as ``unregistered_clusters``.
"""

from __future__ import annotations

from integrations.gcp.gke.autoregister import (
    env_declares_kubernetes,
    register_now,
    requested_scope,
    start_gke_autoregistration,
)
from integrations.gcp.gke.discovery import DiscoveredCluster, Discovery, discover_clusters
from integrations.gcp.gke.kubeconfig import AUTH_PLUGIN, build_kubeconfig, plugin_installed
from integrations.gcp.gke.registration import (
    ClusterRegistration,
    Outcome,
    RegistrationReport,
    register_gke_clusters,
)
from integrations.gcp.gke.scope import (
    ANY,
    ClusterScope,
    ScopeSpec,
    parse_scopes,
    scopes_from_cluster_names,
)

__all__ = [
    "ANY",
    "AUTH_PLUGIN",
    "ClusterScope",
    "ClusterRegistration",
    "DiscoveredCluster",
    "Discovery",
    "Outcome",
    "RegistrationReport",
    "ScopeSpec",
    "build_kubeconfig",
    "discover_clusters",
    "env_declares_kubernetes",
    "parse_scopes",
    "plugin_installed",
    "register_gke_clusters",
    "register_now",
    "scopes_from_cluster_names",
    "requested_scope",
    "start_gke_autoregistration",
]
