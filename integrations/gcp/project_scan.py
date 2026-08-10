"""One project-listing pass across every registered GCP credential.

Shared by ``gcp_list_projects``, which reports the scope, and
``gcp_refresh_discovery``, which re-reads it. Two copies of this loop would be
two answers to "what can this deployment see", differing in whichever detail got
fixed in one and not the other: the dedupe order, which failure is kept, whether
configured projects stay ahead of discovered ones.

Sits above :mod:`integrations.gcp.tool_params` in the import order — it needs
``config_from`` to rehydrate a credential — so nothing in the discovery path may
import it back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.tool_framework.telemetry import report_run_error
from integrations.gcp.project_discovery import DiscoveryResult, discover, reset_cache
from integrations.gcp.projects import group_projects
from integrations.gcp.tool_params import config_from

#: What to tell the agent when a visible project is not on the allow-list. The
#: fix is configuration, so it names both ways of applying it — one project at a
#: time, or every project this credential can see.
UNUSABLE_NOTE = (
    "Projects outside configured_projects are visible but not yet queryable. "
    "Add them to GCP_ADDITIONAL_PROJECTS by id, or set "
    "GCP_ADDITIONAL_PROJECTS=discover to allow everything listed here."
)


@dataclass(frozen=True)
class ScanResult:
    """What one pass found.

    ``failure`` is at most one, not one per credential: a deployment whose
    credentials all lack ``resourcemanager.projects.list`` would otherwise
    report — and, for the caller that sends it to Sentry, page on — an identical
    error per instance on every single call.
    """

    configured: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    discovered: list[str] = field(default_factory=list)
    failure: DiscoveryResult | None = None

    @property
    def unusable(self) -> list[str]:
        """Discovered projects the other GCP tools would still refuse.

        Visible is not queryable: ``resolve_projects`` accepts only what is
        configured. Naming the gap is what stops the agent inferring that
        everything listed can be queried, and then reading "unknown GCP
        project" as the project not existing.

        Compared against ``configured`` rather than against
        ``projects`` minus ``discovered`` — a project that is both configured
        *and* discovered is in both lists, and that subtraction would report it
        as unusable when it is the most usable project there is.
        """
        allowed = set(self.configured)
        return [project for project in self.discovered if project not in allowed]


def scan_projects(
    configured: list[str],
    project_configs: dict[str, Any] | None,
    *,
    force: bool = False,
) -> ScanResult:
    """List what every registered credential can see, merged with ``configured``.

    One listing per credential: each sees a different slice of the resource
    hierarchy, so asking only the default one under-reports.

    ``force`` discards the cache first, so the listing is read from Google
    rather than from whatever the last call left behind. It is the whole point
    of ``gcp_refresh_discovery`` and wrong everywhere else — it drops the last
    good listing along with the stale one, so a forced pass that then fails
    falls back to configured-only.
    """
    if force:
        reset_cache()

    discovered: list[str] = []
    seen_discovered: set[str] = set()
    failure: DiscoveryResult | None = None
    for config_payload, group in group_projects(configured, project_configs):
        listing = discover(config_from(config_payload, fallback_project=group[0]))
        if listing.error and failure is None:
            failure = listing
        for project in listing.projects:
            if project not in seen_discovered:
                seen_discovered.add(project)
                discovered.append(project)

    # Configured first, in the operator's order: the head of this list is what
    # a caller that omits ``project`` gets, and that must stay their choice
    # rather than whichever project Resource Manager happened to return first.
    merged = list(configured)
    seen = set(merged)
    for project in discovered:
        if project not in seen:
            seen.add(project)
            merged.append(project)

    return ScanResult(
        configured=list(configured),
        projects=merged,
        discovered=discovered,
        failure=failure,
    )


def report_scan_failure(scan: ScanResult, *, tool_name: str, component: str) -> None:
    """Send a failed listing to Sentry under the *calling tool's* name, at ``warning``.

    Warning, not error: a missing ``resourcemanager.projects.list`` grant is a
    configuration choice, not a defect, and the caller still answers from the
    configured scope.

    Tagged by the caller rather than by this module because
    :mod:`integrations.gcp.project_discovery` also serves allow-list expansion,
    which is not a tool call and has no name to report under — reporting
    centrally would have to invent one, and every discovery failure in the
    process would then be filed against whatever that constant said.
    """
    if scan.failure is None or scan.failure.exception is None:
        # No failure, or a GCPClientError — a credential that never built. That
        # is already surfaced in the result and is not a runtime fault to page on.
        return
    report_run_error(
        scan.failure.exception,
        tool_name=tool_name,
        source="gcp",
        component=component,
        method="cloudresourcemanager.projects.list",
        severity="warning",
    )


__all__ = ["UNUSABLE_NOTE", "ScanResult", "report_scan_failure", "scan_projects"]
