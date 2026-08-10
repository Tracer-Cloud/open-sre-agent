"""Read a Cloud Monitoring metrics scope, from either direction.

A metrics scope is not a data boundary. Querying a *scoping* project's time
series, alerts, alert policies and SLOs returns them for every project the
scope monitors, each attributed by ``resource.labels.project_id``. That is why
a project id named in an alert is a **monitoring scope, not a runtime
location** — many estates create monitors in a dedicated observability project
while the services run elsewhere, and just as many co-locate them. Nothing in
the alert says which, and concluding "that project has nothing in it" from a
query against the wrong one is the failure this tool exists to prevent.

``monitoring/v1`` is deliberate: ``monitoring/v3`` has no ``metricsScopes``
resource at all. The two versions expose disjoint resource sets rather than one
superseding the other, which is why :mod:`integrations.gcp.client` carries both
constants.
"""

from __future__ import annotations

from typing import Any

from config.constants.gcp import GCP_UNENTITLED_STATUSES
from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    MONITORING_SCOPE_API,
    RESOURCE_MANAGER_API,
    GCPClientError,
    api_not_enabled,
    api_status,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import resolve_projects
from integrations.gcp.tool_params import config_from, gcp_tool_params
from integrations.gcp.tools.gcp_metrics_scope_tool.scopes import (
    ProjectIdIndex,
    attach_project_ids,
    normalize_monitored,
    normalize_scopes,
    project_id_index,
    scope_project_number,
    scope_resource_name,
)

_COMPONENT = "integrations.gcp.tools.gcp_metrics_scope_tool"

MONITORED_PROJECTS = "monitored_projects"
CONTAINING_SCOPES = "containing_scopes"
_DIRECTIONS = (MONITORED_PROJECTS, CONTAINING_SCOPES)

#: Discovery method ids, used as the Sentry ``method`` tag.
_GET_METHOD = "monitoring.locations.global.metricsScopes.get"
_REVERSE_METHOD = "monitoring.locations.global.metricsScopes.listMetricsScopesByMonitoredProject"
_BUILD_METHOD = "monitoring.discovery.build"
_RESOURCE_MANAGER_METHOD = "cloudresourcemanager.projects.list"

#: Long prose extracted from tuple displays to avoid implicit string concatenation.
_ANTI_EXAMPLE_LIST_PROJECTS = (
    "Do not use this to list every project the credential can reach — that is "
    "gcp_list_projects. This answers a Cloud Monitoring question about one scope."
)

_ANTI_EXAMPLE_SINGLE_SCOPE = (
    "Do not pass a list or '*'. A metrics scope is a single project by "
    "definition, and one scope already answers for every project it monitors."
)

_ANTI_EXAMPLE_EMPTY_RESULT = (
    "Do not treat an empty or self-only result as 'the project has no data'. It "
    "means the project monitors only itself, which is the ordinary co-located "
    "topology — query it directly."
)

#: Long prose held as module constants rather than split across adjacent
#: literals at the call site, where an implicit concatenation inside a list or
#: call display reads as a missing comma.
_SELF_SCOPED_NOTE = (
    "This project monitors only itself, so its metrics and alerts are its own. "
    "Signals for a workload elsewhere are not here."
)
_UNRESOLVED_NOTE = (
    "Cloud Monitoring returns project numbers, not ids. Resource Manager could "
    "not be listed, so ids are blank — grant resourcemanager.projects.list, or "
    "match the numbers by hand."
)
_TRUNCATED_PROJECTS_NOTE = (
    "Cloud Monitoring returns project numbers, not ids. The Resource Manager "
    "listing was capped before it reached the end, so some ids are blank. That "
    "is a size limit on this estate, not a missing permission — name the "
    "projects you need rather than re-granting access."
)
_NO_CONTAINING_SCOPE_NOTE = (
    "No other project's metrics scope contains this one, so its metrics and "
    "alerts are read from the project itself."
)

_ANTI_EXAMPLES = (
    _ANTI_EXAMPLE_LIST_PROJECTS,
    _ANTI_EXAMPLE_SINGLE_SCOPE,
    _ANTI_EXAMPLE_EMPTY_RESULT,
)

_USE_CASES = (
    "An alert names a project you cannot place: find out which projects it monitors",
    "Deciding whether querying an observability project will reach the workload's signals",
    "Finding the observability project that holds a workload project's metrics and alerts",
    "Attributing a metrics-scope result back to the project each series came from",
    "Learning which monitored projects are not yet in GCP_ADDITIONAL_PROJECTS",
)

_DESCRIPTION = (
    "Read a Cloud Monitoring metrics scope: which projects a scoping project "
    "monitors, or which scopes a workload project belongs to. A metrics scope "
    "is not a data boundary — querying a scoping project's time series, alerts "
    "and SLOs returns them for every project in its scope. Use this to decide "
    "whether a project named in an alert is an observability project holding "
    "signals for workloads that run elsewhere, or just itself."
)

_PROJECT_DESCRIPTION = (
    "One GCP project id. Not a list and not '*' — a metrics scope is a single "
    "project. For direction='monitored_projects' pass the project the alert "
    "named (the candidate scoping project). For direction='containing_scopes' "
    "pass the project the workload runs in. Omit for the default project. Call "
    "gcp_list_projects for valid names."
)

_DIRECTION_DESCRIPTION = (
    "monitored_projects: given a scoping project, list the projects it "
    "monitors. containing_scopes: given a workload project, list every scope "
    "it has been added to — this is how you find the observability project "
    "that holds its metrics and alerts."
)


def _unresolved_numbers_note(count: int) -> str:
    """Prose for monitored projects Resource Manager could not put a name to."""
    return (
        f"{count} monitored project(s) could not be matched to a project id, so "
        "they are listed in monitored_projects but appear in neither "
        "queryable_project_ids nor unconfigured_project_ids. A deleted project, "
        "or one outside this credential's Resource Manager reach, looks exactly "
        "like this."
    )


def _unresolved_scopes_note(count: int) -> str:
    """Prose for containing scopes Resource Manager could not put a name to."""
    return (
        f"{count} containing scope(s) could not be matched to a project id, so "
        "they are listed in containing_scopes but not in "
        "observability_project_ids. A deleted project, or one outside this "
        "credential's Resource Manager reach, looks exactly like this."
    )


def _unconfigured_note(count: int) -> str:
    """Prose for monitored projects that are not in the configured allow-list."""
    return (
        f"{count} monitored project(s) are in this scope but not in "
        "GCP_ADDITIONAL_PROJECTS, so they cannot be queried directly. Their "
        "series are still reachable through this scoping project."
    )


def _disabled_api_message(target: str) -> str:
    """Prose for a project that never switched Cloud Monitoring on."""
    return (
        f"the Cloud Monitoring API is not enabled in {target}, so it has no "
        "metrics scope to read. Enable monitoring.googleapis.com there, or ask "
        "about a different project."
    )


def _too_many_projects_message(count: int) -> str:
    """Prose for a caller that asked for more than one scope at a time."""
    return (
        f"gcp_metrics_scope reads one metrics scope per call; got {count}. "
        "A scope already covers every project it monitors."
    )


def _unavailable(error: str, **extra: Any) -> dict[str, Any]:
    """Unavailable envelope carrying both result keys, always."""
    return tool_unavailable("gcp", error, monitored_projects=[], containing_scopes=[], **extra)


def _failed(error: str, target: str, direction: str) -> dict[str, Any]:
    """Failure envelope carrying both result keys, always."""
    return {
        "found": False,
        "error": error,
        "project": target,
        "direction": direction,
        "monitored_projects": [],
        "containing_scopes": [],
    }


@tool(
    name="gcp_metrics_scope",
    display_name="Cloud Monitoring metrics scope",
    source="gcp",
    description=_DESCRIPTION,
    use_cases=list(_USE_CASES),
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            # Deliberately not PROJECT_PROPERTY: that advertises '*' and
            # comma-separated lists and tells the model to sweep when unsure.
            # Both are meaningless against a single scope, and one scope
            # already answers for every project it monitors.
            "project": {
                "type": "string",
                "default": "",
                "description": _PROJECT_DESCRIPTION,
            },
            "direction": {
                "type": "string",
                "enum": list(_DIRECTIONS),
                "default": MONITORED_PROJECTS,
                "description": _DIRECTION_DESCRIPTION,
            },
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_metrics_scope(
    project: str = "",
    direction: str = MONITORED_PROJECTS,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    # ``gcp_tool_params`` also injects ``limit``. A metrics scope is one bounded
    # document, so there is nothing here to page — accept and ignore, matching
    # ``gcp_list_projects``.
    **_injected: Any,
) -> dict[str, Any]:
    """Read one Cloud Monitoring metrics scope."""
    wanted = (direction or MONITORED_PROJECTS).strip()
    if wanted not in _DIRECTIONS:
        return _unavailable(
            f"unknown direction '{direction}'; valid values: {', '.join(_DIRECTIONS)}"
        )

    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return _unavailable(error)
    # A metrics scope is a single project by definition, and the v1 endpoint is
    # scoped to one by URL. Refuse rather than silently reading only the first:
    # a narrowed answer here reads as authoritative and is not.
    if len(projects) > 1:
        return _unavailable(_too_many_projects_message(len(projects)))

    target = projects[0]

    try:
        config = config_from((project_configs or {}).get(target), fallback_project=target)
        service = build_service(config, MONITORING_SCOPE_API)
    except GCPClientError as exc:
        return _unavailable(str(exc))
    except Exception as exc:
        report_run_error(
            exc,
            tool_name="gcp_metrics_scope",
            source="gcp",
            component=_COMPONENT,
            method=_BUILD_METHOD,
            extras={"project": target, "direction": wanted},
        )
        return _failed(describe_api_error(exc), target, wanted)

    # ``global`` is a Python keyword, so googleapiclient's fix_method_name
    # renames the resource to ``global_``. Reaching it any other way is a
    # SyntaxError or an AttributeError, not a working call.
    scopes_resource = service.locations().global_().metricsScopes()
    try:
        if wanted == MONITORED_PROJECTS:
            payload = scopes_resource.get(name=scope_resource_name(target)).execute()
        else:
            payload = scopes_resource.listMetricsScopesByMonitoredProject(
                monitoredResourceContainer=f"projects/{target}"
            ).execute()
    except Exception as exc:
        if api_not_enabled(exc):
            # A project that never enabled Monitoring has no scope to read.
            # That is an answer, not a fault, and reporting it would file one
            # Sentry error per turn on an estate where most projects are off.
            return _unavailable(
                _disabled_api_message(target),
                api_enabled=False,
                project=target,
                direction=wanted,
            )
        report_run_error(
            exc,
            tool_name="gcp_metrics_scope",
            source="gcp",
            component=_COMPONENT,
            method=_GET_METHOD if wanted == MONITORED_PROJECTS else _REVERSE_METHOD,
            severity="warning",
            extras={"project": target, "direction": wanted},
        )
        return _failed(describe_api_error(exc), target, wanted)

    # Best effort, and after the call above so a failing build_service reports
    # exactly one event. Cloud Monitoring answers in project numbers; without
    # this the model gets digits it cannot pass to any other GCP tool.
    try:
        index_result = project_id_index(build_service(config, RESOURCE_MANAGER_API))
    except Exception as exc:  # noqa: BLE001 — id resolution is decoration, never the answer
        index_result = ProjectIdIndex(mapping={}, truncated=False, error=exc)

    # Degrading silently is right for a denial and wrong for a fault. Blank ids
    # look identical either way, and the note the reader gets tells them to grant
    # a permission — advice that is useless if Resource Manager was actually
    # returning 500s. Report the faults; stay quiet about the posture.
    if (
        index_result.error is not None
        and api_status(index_result.error) not in GCP_UNENTITLED_STATUSES
    ):
        report_run_error(
            index_result.error,
            tool_name="gcp_metrics_scope",
            source="gcp",
            component=_COMPONENT,
            method=_RESOURCE_MANAGER_METHOD,
            severity="warning",
            extras={"project": target, "direction": wanted},
        )

    index = index_result.mapping
    resolved = bool(index)

    if wanted == MONITORED_PROJECTS:
        return _monitored_response(
            target, payload, index, resolved, index_result.truncated, available_projects
        )
    return _containing_response(target, payload, index, resolved, index_result.truncated)


def _monitored_response(
    target: str,
    payload: dict[str, Any],
    index: dict[str, str],
    resolved: bool,
    truncated: bool,
    available_projects: list[str] | None,
) -> dict[str, Any]:
    """Shape a ``direction='monitored_projects'`` result."""
    scope_name = str(payload.get("name", "") or "")
    scope_number = scope_project_number(scope_name)

    rows = normalize_monitored(payload.get("monitoredProjects") or [], scope_number)
    attach_project_ids(rows, index, "project_number", "project_id")

    # The field that earns the tool: True is the co-located topology, False
    # with more than one entry is the split one.
    self_scoped = len(rows) == 1 and rows[0]["is_scoping_project"]

    # The scoping project is the one that was just queried, so it is neither
    # news nor something to add to an allow-list. Both lists describe the
    # *other* projects the scope reaches.
    configured = set(available_projects or [])
    others = [row for row in rows if not row["is_scoping_project"]]
    ids = [row["project_id"] for row in others if row["project_id"]]
    queryable = [project_id for project_id in ids if project_id in configured]
    unconfigured = [project_id for project_id in ids if project_id not in configured]
    # A row whose number never resolved belongs to neither list above, so
    # without this field it is in the response and in nothing a reader acts on.
    # Both lists are keyed by id, and there is no honest id to put in them.
    unresolved = [row["project_number"] for row in others if not row["project_id"]]

    if self_scoped:
        note = _SELF_SCOPED_NOTE
    elif not resolved:
        note = _UNRESOLVED_NOTE
    elif truncated and unresolved:
        # The cap is worth saying only when it left a blank behind. Gated rather
        # than ranked unconditionally: a listing that was capped *and* resolved
        # every project asked for would otherwise hand the reader a caveat that
        # does not apply — and suppress the one note they can act on.
        note = _TRUNCATED_PROJECTS_NOTE
    elif unresolved:
        # Ranked above the unconfigured note: an unconfigured project is a known
        # gap the reader is already being handed, whereas an unresolved one is
        # invisible unless this says so.
        note = _unresolved_numbers_note(len(unresolved))
    elif unconfigured:
        note = _unconfigured_note(len(unconfigured))
    else:
        note = ""

    return {
        "found": True,
        "project": target,
        "direction": MONITORED_PROJECTS,
        "scope_name": scope_name,
        "scope_project_number": scope_number,
        "self_scoped": self_scoped,
        "monitored_project_count": len(rows),
        "monitored_projects": rows,
        "queryable_project_ids": queryable,
        "unconfigured_project_ids": unconfigured,
        "unresolved_project_numbers": unresolved,
        "project_ids_resolved": resolved,
        "note": note,
        # Present on every path so a consumer never key-errors on shape.
        "containing_scopes": [],
    }


def _containing_response(
    target: str,
    payload: dict[str, Any],
    index: dict[str, str],
    resolved: bool,
    truncated: bool,
) -> dict[str, Any]:
    """Shape a ``direction='containing_scopes'`` result."""
    rows = normalize_scopes(payload.get("metricsScopes") or [])
    attach_project_ids(rows, index, "scope_project_number", "scope_project_id")

    others = [row for row in rows if not row["is_self"]]
    observability = [row["scope_project_id"] for row in others if row["scope_project_id"]]
    # The same gap ``unresolved_project_numbers`` closes on the monitored side,
    # and worse here: an empty ``observability_project_ids`` is byte-identical to
    # "this project sits in no other metrics scope", which is the exact wrong
    # conclusion for the one question this direction exists to answer.
    unresolved = [row["scope_project_number"] for row in others if not row["scope_project_id"]]

    if len(rows) <= 1:
        note = _NO_CONTAINING_SCOPE_NOTE
    elif not resolved:
        note = _UNRESOLVED_NOTE
    elif truncated and unresolved:
        note = _TRUNCATED_PROJECTS_NOTE
    elif unresolved:
        note = _unresolved_scopes_note(len(unresolved))
    else:
        note = ""

    return {
        "found": True,
        "project": target,
        "direction": CONTAINING_SCOPES,
        "scope_count": len(rows),
        "containing_scopes": rows,
        "observability_project_ids": observability,
        "unresolved_scope_numbers": unresolved,
        "project_ids_resolved": resolved,
        "note": note,
        "monitored_projects": [],
    }
