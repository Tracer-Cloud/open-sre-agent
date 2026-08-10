"""Register discovered GKE clusters as named Kubernetes instances.

``gcp_list_gke_clusters`` reports which clusters have no kubeconfig registered
and therefore cannot be reached by the ``kubernetes_*`` tools. That report is a
dead end for the operator unless something closes it, which is what this does:
discover, synthesize a kubeconfig, and hand it to
:func:`integrations.kubernetes.clusters.add_cluster` — the same path
``opensre integrations add-cluster`` already uses, so verification, storage and
naming behave identically whether a cluster was added by hand or found here.

Pure orchestration: discovery lives in :mod:`.discovery`, kubeconfig synthesis
in :mod:`.kubeconfig`, and persistence in the Kubernetes integration. Nothing
here talks to an API or a store directly, which is why the whole flow is
testable with two fakes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from integrations.gcp.gke.discovery import DiscoveredCluster, discover_clusters
from integrations.gcp.gke.kubeconfig import build_kubeconfig, credentials_path_for
from integrations.gcp.gke.scope import ScopeSpec
from integrations.gcp.projects import resolve_projects
from integrations.gcp.tool_params import gcp_tool_params, sanitize_config
from integrations.kubernetes.clusters import add_cluster, list_clusters


class Outcome(StrEnum):
    """What happened to one discovered cluster."""

    REGISTERED = "registered"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class ClusterRegistration:
    """The result of trying to register one cluster."""

    cluster: str
    project: str
    instance: str
    outcome: Outcome
    detail: str


@dataclass
class RegistrationReport:
    """Everything a surface needs to render the run."""

    results: list[ClusterRegistration] = field(default_factory=list)
    #: Project-level discovery failures — a project that could not be listed at
    #: all, as opposed to a cluster that could not be registered.
    errors: list[str] = field(default_factory=list)
    #: ``cluster (project)`` for every cluster discovery found and the scope
    #: filtered out. Named rather than counted because the way a cluster filter
    #: fails is a typo in a cluster name, and "registered 0" on its own gives an
    #: operator nothing to compare their spelling against.
    excluded: list[str] = field(default_factory=list)
    #: Projects with no Kubernetes Engine API, which therefore cannot hold a
    #: cluster. Not an error, but still named: the way a project selector fails
    #: is a project that was expected to have clusters and turns out not to.
    no_gke: list[str] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        """Return how many clusters ended in ``outcome``."""
        return sum(1 for result in self.results if result.outcome is outcome)


#: Stamped on every instance this flow creates, so an operator can tell an
#: auto-registered cluster from a hand-added one and filter on it later.
_SOURCE_TAG = "gke"

_PRIVATE_ENDPOINT_NOTE = (
    "cluster has a private endpoint only; it is reachable from inside its VPC. "
    "Re-run from a host with VPC access, or use --no-verify to register it anyway"
)


def _instance_names(clusters: list[DiscoveredCluster]) -> dict[str, str]:
    """Map each cluster's context to the instance name it should be registered under.

    The cluster's own name is the friendly choice and the one an operator would
    type. It is not unique across projects, though — ``prod`` in two projects is
    ordinary — so a name claimed by more than one discovered cluster is
    qualified with the project. Only the ambiguous ones pay that cost.
    """
    duplicated = {
        name for name, total in Counter(cluster.name for cluster in clusters).items() if total > 1
    }
    return {
        cluster.context: (
            f"{cluster.name}-{cluster.project}" if cluster.name in duplicated else cluster.name
        )
        for cluster in clusters
    }


def _existing(registered: list[Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(context -> instance, instance -> context)`` for what is already stored."""
    by_context: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for summary in registered:
        name = str(getattr(summary, "name", "")).strip()
        if not name:
            continue
        context = str(getattr(summary, "context", "")).strip()
        by_name[name.lower()] = context
        if context:
            by_context.setdefault(context, name)
    return by_context, by_name


def _kubeconfig_for(cluster: DiscoveredCluster, identity: tuple[str, str]) -> str:
    credentials_path, impersonate_service_account = identity
    return build_kubeconfig(
        context=cluster.context,
        endpoint=cluster.endpoint,
        ca_certificate=cluster.ca_certificate,
        credentials_path=credentials_path,
        impersonate_service_account=impersonate_service_account,
    )


def _exec_identity(project_configs: dict[str, dict[str, Any]], project: str) -> tuple[str, str]:
    """Return ``(service-account key path, impersonated account)`` for ``project``.

    These are the parts of the project's credential settings the auth plugin can
    be told about, so the kubeconfig authenticates as the identity that
    discovered the cluster rather than whatever the plugin resolves alone.
    """
    config = sanitize_config(project_configs.get(project, {}))
    return (
        credentials_path_for(str(config.get("service_account_key", ""))),
        str(config.get("impersonate_service_account", "")).strip(),
    )


def register_gke_clusters(
    *,
    resolved: dict[str, Any],
    project: str = "",
    cluster_scope: ScopeSpec | None = None,
    tags: dict[str, str] | None = None,
    overwrite: bool = False,
    verify: bool = True,
    dry_run: bool = False,
) -> RegistrationReport:
    """Discover GKE clusters in ``project`` and register the unregistered ones.

    ``project`` follows the same grammar as the GCP tools: empty for the default
    project, a comma-separated list, or ``*`` for every configured project. It
    decides what is *discovered*.

    ``cluster_scope`` decides what is then *registered*, for the case a project holds
    clusters the agent should not be handed. ``None`` — the default — registers
    everything discovered. The two must agree about projects or the run is a
    silent no-op, which is why :attr:`ScopeSpec.project_selector` exists: a
    caller holding a spec should pass it as ``project`` rather than composing a
    second list by hand.

    Already-registered clusters are skipped by matching on kubeconfig context,
    which makes re-running the command idempotent. A cluster whose *name* is
    taken by a different context is also skipped unless ``overwrite`` is set —
    silently repointing an existing instance would change where every subsequent
    ``kubernetes_*`` call lands.
    """
    report = RegistrationReport()
    scope = gcp_tool_params(resolved)
    projects, error = resolve_projects(
        project,
        default_project=str(scope.get("default_project", "")),
        available_projects=list(scope.get("available_projects") or []),
    )
    if error:
        report.errors.append(error)
        return report

    project_configs: dict[str, dict[str, Any]] = scope.get("project_configs") or {}
    discovery = discover_clusters(projects, project_configs)
    clusters = discovery.clusters
    report.errors.extend(discovery.errors)
    report.no_gke.extend(discovery.no_gke)

    if cluster_scope is not None:
        admitted: list[DiscoveredCluster] = []
        for cluster in clusters:
            if cluster_scope.admits(cluster.project, cluster.name):
                admitted.append(cluster)
            else:
                report.excluded.append(f"{cluster.name} ({cluster.project})")
        clusters = admitted

    # Deliberately after the filter: `_instance_names` qualifies a name only when
    # two clusters being registered share it, and a cluster the scope excluded is
    # not being registered. Naming one `prod-acme` because of a `prod` the
    # operator explicitly kept out would make the scope they asked for change the
    # name they have to type. Widening the scope later can then collide with the
    # instance this run created — that path is already handled, by name, with an
    # `--overwrite` instruction rather than a silent repoint.
    names = _instance_names(clusters)
    by_context, by_name = _existing(list_clusters())

    for cluster in clusters:
        instance = names[cluster.context]
        already = by_context.get(cluster.context)
        if already:
            report.results.append(
                ClusterRegistration(
                    cluster.name,
                    cluster.project,
                    already,
                    Outcome.SKIPPED,
                    "already registered",
                )
            )
            continue
        conflict = by_name.get(instance.lower())
        if conflict is not None and not overwrite:
            report.results.append(
                ClusterRegistration(
                    cluster.name,
                    cluster.project,
                    instance,
                    Outcome.SKIPPED,
                    f"an instance named '{instance}' already points at another cluster; "
                    "pass --overwrite to replace it",
                )
            )
            continue
        if not cluster.ca_certificate or not cluster.endpoint:
            # Both come back empty when the principal can list clusters but not
            # read their master config; a kubeconfig without them cannot connect.
            report.results.append(
                ClusterRegistration(
                    cluster.name,
                    cluster.project,
                    instance,
                    Outcome.FAILED,
                    "the API returned no endpoint or CA certificate for this cluster",
                )
            )
            continue
        if dry_run:
            report.results.append(
                ClusterRegistration(
                    cluster.name, cluster.project, instance, Outcome.REGISTERED, "would register"
                )
            )
            continue

        result = add_cluster(
            name=instance,
            kubeconfig=_kubeconfig_for(cluster, _exec_identity(project_configs, cluster.project)),
            context=cluster.context,
            tags={
                "source": _SOURCE_TAG,
                "project": cluster.project,
                "location": cluster.location,
                **(tags or {}),
            },
            verify=verify,
        )
        detail = result.detail
        if not result.ok and cluster.private_endpoint_only:
            detail = f"{detail} ({_PRIVATE_ENDPOINT_NOTE})"
        report.results.append(
            ClusterRegistration(
                cluster.name,
                cluster.project,
                instance,
                Outcome.REGISTERED if result.ok else Outcome.FAILED,
                detail,
            )
        )
        if result.ok:
            # Keep the local view current so a second cluster in the same run
            # cannot claim a name this one just took.
            by_name[instance.lower()] = cluster.context
            by_context[cluster.context] = instance

    return report
