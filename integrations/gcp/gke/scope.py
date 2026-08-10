"""The ``project[/cluster]`` grammar that bounds GKE registration.

Registration used to be scoped by project alone, which is the wrong granularity
for the case that matters: a project holding one cluster you want investigated
and several you do not. There was no way to say so — the choice was every
cluster in the project or none of it — and "none of it" is what an operator
picks when the alternative is handing the agent clusters it has no business
restarting pods in.

A scope is one project, optionally narrowed to one cluster inside it. A spec is
a list of scopes, and a cluster is in scope if **any** of them admits it, so
``prod-a/checkout,prod-b`` reads exactly as it looks: one named cluster from the
first project, all of the second.

Cluster names are qualified by project on purpose. Two clusters called ``prod``
in different projects is ordinary — the registration path already renames them
``prod-<project>`` for precisely that reason — so an unqualified filter would
quietly register a same-named cluster in a project the operator never had in
mind. Use ``*/name`` to say "wherever it lives"; say it deliberately.

Pure data and string handling: no I/O, no environment reads. The environment
name and its ``true``/``false`` spellings belong to
:mod:`integrations.gcp.gke.autoregister`, and the CLI builds a spec from flags,
so both surfaces share one definition of what a scope means.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Wildcard for "any configured project", spelled the same as in the project
#: grammar the GCP tools already take so an operator learns it once.
ANY = "*"

#: Separates the project from the cluster within one scope.
SEPARATOR = "/"

#: Separates scopes.
DELIMITER = ","


@dataclass(frozen=True)
class ClusterScope:
    """One project, optionally narrowed to a single cluster inside it."""

    project: str
    cluster: str = ""

    def admits(self, project: str, cluster: str) -> bool:
        """Whether the cluster named ``cluster`` in ``project`` is inside this scope."""
        if self.project != ANY and self.project != project.strip().casefold():
            return False
        return not self.cluster or self.cluster == cluster.strip().casefold()

    def __str__(self) -> str:
        return f"{self.project}{SEPARATOR}{self.cluster}" if self.cluster else self.project


@dataclass(frozen=True)
class ScopeSpec:
    """Everything registration is allowed to touch, as a list of scopes.

    An empty spec is not "nothing" — it is "unscoped", the same default
    ``register_gke_clusters`` has always had: the default project, every cluster
    in it. Refusing to register anything on an empty spec would turn a parse that
    found no usable entries into a silent no-op, which is the failure this module
    is meant to make impossible to hit by accident.
    """

    scopes: tuple[ClusterScope, ...] = ()

    @property
    def project_selector(self) -> str:
        """The projects to discover, in the grammar every GCP tool already takes.

        Derived rather than configured separately: a caller that passed its own
        project list alongside a spec could disagree with it, and the symptom of
        that — discovery sweeping projects the filter then rejects wholesale — is
        zero registrations and nothing in the log that says why.
        """
        if not self.scopes:
            return ""
        if any(scope.project == ANY for scope in self.scopes):
            return ANY
        # dict.fromkeys, not set(): the order an operator wrote is the order the
        # report reads back, and discovery groups projects by credential anyway.
        return DELIMITER.join(dict.fromkeys(scope.project for scope in self.scopes))

    @property
    def names_clusters(self) -> bool:
        """Whether any scope narrows to a specific cluster."""
        return any(scope.cluster for scope in self.scopes)

    def admits(self, project: str, cluster: str) -> bool:
        """Whether this cluster may be registered."""
        if not self.scopes:
            return True
        return any(scope.admits(project, cluster) for scope in self.scopes)

    def __str__(self) -> str:
        return DELIMITER.join(str(scope) for scope in self.scopes) if self.scopes else ANY


def parse_scopes(raw: str) -> ScopeSpec:
    """Parse ``proj``/``proj/cluster``/``*/cluster`` entries into a spec.

    Tolerant by design — a malformed entry is dropped rather than raising, and a
    value that yields nothing usable parses to an unscoped spec. This runs at
    process start inside a daemon thread, where the alternative to tolerance is
    an exception nobody sees.

    Blank halves widen rather than narrow, which is the safe direction to guess
    in for a filter: ``proj/`` is every cluster in ``proj``, and ``/name`` is
    that cluster in any project, the same as ``*/name``.
    """
    scopes: list[ClusterScope] = []
    for entry in raw.split(DELIMITER):
        candidate = entry.strip().casefold()
        if not candidate:
            continue
        project, separator, cluster = candidate.partition(SEPARATOR)
        project = project.strip()
        if separator and not project:
            project = ANY
        if not project:
            continue
        scope = ClusterScope(project=project, cluster=cluster.strip())
        if scope not in scopes:
            scopes.append(scope)
    return ScopeSpec(tuple(scopes))


def scopes_from_cluster_names(clusters: tuple[str, ...]) -> ScopeSpec:
    """Build a filter-only spec from the CLI's repeatable ``--cluster`` flag.

    Every scope is left at :data:`ANY` because ``--cluster`` does not choose
    projects — ``--project`` already did, and re-deriving the project set here
    would give the command two answers to the same question. Discovery has
    therefore already narrowed to those projects by the time this filter runs, so
    a bare name cannot reach outside them.

    That is also why this does not go through :func:`parse_scopes`: a value
    arriving through ``--cluster`` is a cluster name, so a ``/`` in it is a typo
    rather than a project qualifier, and silently reinterpreting it would send
    the command at a project the operator never named.
    """
    named = dict.fromkeys(name.strip().casefold() for name in clusters if name.strip())
    return ScopeSpec(tuple(ClusterScope(project=ANY, cluster=name) for name in named))


__all__ = [
    "ANY",
    "ClusterScope",
    "ScopeSpec",
    "parse_scopes",
    "scopes_from_cluster_names",
]
