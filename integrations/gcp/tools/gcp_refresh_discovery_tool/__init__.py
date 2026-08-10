"""Re-read the GCP estate now, rather than at the next refresh interval.

Project discovery and GKE auto-registration both refresh on a timer, which
bounds how stale they get but says nothing about *when* they update. This tool
is the answer to the case the timer handles badly: an operator has just created
a project, or granted the service account access to one, or spun up a cluster,
and wants to investigate it in the next message — not in up to half an hour.

**It grants nothing.** Both halves re-run work the deployment already does on
its own, with the scope it is already configured for:
``GCP_ADDITIONAL_PROJECTS`` still decides which discovered projects become
queryable, and GKE registration does nothing at all unless
``GCP_AUTO_REGISTER_GKE`` was already set. Calling this after widening either
variable does nothing either — the process reads its environment at start.
What it changes is timing, and only timing.
"""

from __future__ import annotations

import logging
from typing import Any

from config.constants.gcp import GCP_ADDITIONAL_PROJECTS_ENV, GCP_AUTO_REGISTER_GKE_ENV
from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.gke.autoregister import (
    RegistrationSummary,
    register_now,
    requested_scope,
)
from integrations.gcp.project_scan import UNUSABLE_NOTE, report_scan_failure, scan_projects
from integrations.gcp.tool_params import gcp_tool_params

logger = logging.getLogger(__name__)

_COMPONENT = "integrations.gcp.tools.gcp_refresh_discovery_tool"

#: Added whenever the refresh found projects that are visible but not allowed —
#: the most likely reason someone runs this and sees nothing change.
_RESTART_NOTE = (
    f"A change to {GCP_ADDITIONAL_PROJECTS_ENV} takes effect on the next restart, not on this call."
)

#: Module constants rather than adjacent literals inside the metadata lists:
#: implicit concatenation in a list display is indistinguishable from a missing
#: comma, which CodeQL flags (``py/implicit-string-concatenation-in-list``).
_ORDER_ANTI_EXAMPLE = (
    "Do not call before gcp_list_projects — read the current scope first, and refresh "
    "only if what you need is missing from it."
)
_SCOPE_ANTI_EXAMPLE = (
    "Do not call to widen access. It re-reads the configured scope and cannot extend "
    "it; changing GCP_ADDITIONAL_PROJECTS or GCP_AUTO_REGISTER_GKE needs a restart."
)
_DESCRIPTION = (
    "Re-read which Google Cloud projects and GKE clusters this deployment can reach, "
    "without waiting for the periodic refresh. Use when a project or cluster was "
    "created or granted access just now. WARNING: This operation is slow (can take "
    "~105 seconds) and runs synchronously during your chat turn. Only use when "
    "absolutely necessary."
)


def _gke_summary(summary: RegistrationSummary) -> dict[str, Any]:
    """Render a completed registration pass for the tool result."""
    if summary.stood_down:
        # Neither success nor failure: a guard declined to run. Reported as its
        # own state because "registered 0" would read as "there is nothing to
        # register", which is the opposite of what happened.
        return {"enabled": True, "ran": False, "detail": summary.stood_down}
    result: dict[str, Any] = {
        "enabled": True,
        "ran": True,
        "registered": summary.registered,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }
    if summary.instances:
        result["new_instances"] = list(summary.instances)
    return result


def _refresh_gke() -> dict[str, Any]:
    """Re-run GKE registration on this thread, if it is switched on at all.

    Synchronous, unlike the boot path, which uses a daemon thread to keep
    Google off the readiness probe. Here the caller *is* waiting for the answer,
    and every Google call this makes is bounded by the transport timeout, so
    there is nothing to protect the process from.
    """
    scope = requested_scope()
    if scope is None:
        return {
            "enabled": False,
            "detail": (
                f"{GCP_AUTO_REGISTER_GKE_ENV} is not set, so no GKE cluster is registered "
                "automatically. Project discovery was refreshed regardless."
            ),
        }
    try:
        return _gke_summary(register_now(logger, scope))
    except Exception as exc:  # noqa: BLE001 — a failed half must not lose the other half
        report_run_error(
            exc,
            tool_name="gcp_refresh_discovery",
            source="gcp",
            component=_COMPONENT,
            method="container.projects.locations.clusters.list",
            severity="warning",
        )
        return {
            "enabled": True,
            "ran": False,
            "error": f"GKE re-registration failed ({type(exc).__name__})",
        }


@tool(
    name="gcp_refresh_discovery",
    display_name="Refresh GCP discovery",
    source="gcp",
    description=_DESCRIPTION,
    use_cases=[
        "A project or GKE cluster created minutes ago is not yet visible to the GCP tools",
        "IAM access to a project was granted during this investigation",
        "gcp_list_projects reported a discovery error that has since been fixed",
    ],
    anti_examples=[_ORDER_ANTI_EXAMPLE, _SCOPE_ANTI_EXAMPLE],
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={"type": "object", "properties": {}, "required": []},
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_refresh_discovery(
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    # ``gcp_tool_params`` is shared by every GCP tool, so it also injects
    # ``limit`` — irrelevant here, but it has to be accepted.
    **_injected: Any,
) -> dict[str, Any]:
    """Force a re-read of the project listing and of GKE registration."""
    configured = list(available_projects or [])
    if default_project and default_project not in configured:
        configured.insert(0, default_project)
    if not configured:
        return tool_unavailable(
            "gcp", "no GCP project is configured; set GCP_PROJECT_ID", projects=[]
        )

    # ``available_projects`` was built before this call from the *cached*
    # listing, so it is the pre-refresh scope by construction — which is what
    # makes the two lists in this result comparable.
    scan = scan_projects(configured, project_configs, force=True)

    result: dict[str, Any] = {
        "default_project": default_project,
        "configured_projects": configured,
        "projects": scan.projects,
        "discovered_projects": scan.discovered,
        "gke": _refresh_gke(),
    }
    if scan.failure is not None:
        report_scan_failure(scan, tool_name="gcp_refresh_discovery", component=_COMPONENT)
        result["discovery_error"] = scan.failure.error
    if scan.unusable:
        result["note"] = f"{UNUSABLE_NOTE} {_RESTART_NOTE}"
    return result
