"""Project discovery tool — how the agent learns valid ``project`` values.

Reports the configured scope, and additionally attempts a live Resource Manager
listing so folder- or organization-level access surfaces projects that were
never named in configuration. The live call is best-effort: ``resourcemanager.
projects.list`` is a permission many service accounts legitimately lack, and a
missing grant must not make the tool useless when the configured scope alone
already answers the question.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.project_scan import UNUSABLE_NOTE, report_scan_failure, scan_projects
from integrations.gcp.tool_params import gcp_tool_params

_COMPONENT = "integrations.gcp.tools.gcp_list_projects_tool"

#: A module constant rather than two adjacent literals inside ``anti_examples``:
#: implicit concatenation in a list display is indistinguishable from a missing
#: comma, which CodeQL flags (``py/implicit-string-concatenation-in-list``).
_REPEAT_ANTI_EXAMPLE = (
    "Do not call repeatedly — the answer is cached, so a second call returns the first "
    "one. Use gcp_refresh_discovery to force a re-read."
)


@tool(
    name="gcp_list_projects",
    display_name="GCP projects",
    source="gcp",
    description=(
        "List the Google Cloud projects this deployment can query. Call before "
        "passing a non-default project to another GCP tool."
    ),
    use_cases=[
        "Discovering valid project values for gcp_logging_query or gcp_monitoring_query",
        "Checking whether a service's project is in scope before investigating it",
    ],
    anti_examples=[_REPEAT_ANTI_EXAMPLE],
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={"type": "object", "properties": {}, "required": []},
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_list_projects(
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    # ``gcp_tool_params`` is shared by all three GCP tools, so it also injects
    # ``limit`` — irrelevant here, but it has to be accepted.
    **_injected: Any,
) -> dict[str, Any]:
    """Return configured and (where permitted) discoverable GCP projects."""
    configured = list(available_projects or [])
    if default_project and default_project not in configured:
        configured.insert(0, default_project)
    if not configured:
        return tool_unavailable(
            "gcp", "no GCP project is configured; set GCP_PROJECT_ID", projects=[]
        )

    result: dict[str, Any] = {
        "default_project": default_project,
        "configured_projects": configured,
        "projects": configured,
    }

    # Cached, deliberately: when ``GCP_ADDITIONAL_PROJECTS=discover`` is on this
    # returns the same listing the allow-list was built from, with no second
    # round trip. Two listings would be two answers, and the one thing this tool
    # must not be wrong about is the allow-list it is reporting on. Use
    # ``gcp_refresh_discovery`` to force a re-read.
    scan = scan_projects(configured, project_configs)

    if scan.failure is not None:
        report_scan_failure(scan, tool_name="gcp_list_projects", component=_COMPONENT)
        result["discovery_error"] = scan.failure.error
        if not scan.discovered:
            # No credential answered, so there is nothing to merge and no
            # `discovered_projects` key to promise. The configured scope is
            # still a correct answer, so this is not an unavailable envelope.
            return result

    result["discovered_projects"] = scan.discovered
    result["projects"] = scan.projects
    if scan.unusable:
        result["note"] = UNUSABLE_NOTE
    return result
