"""Error Reporting — what is actually throwing, ranked, already deduplicated.

The shortest path from "something is wrong" to a stack trace. Error Reporting
clusters exceptions into groups server-side, so one query answers what is
failing, how often, since when, and in which service — work that would otherwise
mean several ``gcp_logging_query`` calls and manual grouping.

``firstSeenTime`` is the field that earns this tool its place: it separates a
regression that started during the incident from a long-standing error with the
same count.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    ERROR_REPORTING_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_error_reporting_tool.groups import normalize_groups

_COMPONENT = "integrations.gcp.tools.gcp_error_reporting_tool"

#: Windows the API accepts, mapped from the plain values a model will reach for.
PERIODS: dict[str, str] = {
    "1h": "PERIOD_1_HOUR",
    "6h": "PERIOD_6_HOURS",
    "1d": "PERIOD_1_DAY",
    "1w": "PERIOD_1_WEEK",
    "30d": "PERIOD_30_DAYS",
}

#: Ranking options, mapped the same way.
ORDERS: dict[str, str] = {
    "count": "COUNT_DESC",
    "last_seen": "LAST_SEEN_DESC",
    "first_seen": "CREATED_DESC",
    "affected_users": "AFFECTED_USERS_DESC",
}

_DEFAULT_PERIOD = "1h"
_DEFAULT_ORDER = "count"

_ANTI_EXAMPLES = (
    "Do not use this to read raw log lines — it reports deduplicated exception "
    "groups. Use gcp_logging_query when you need the surrounding log context.",
    "Do not use this to confirm an error stopped after a fix; a group's count "
    "covers the whole window, so re-query with a window that starts after the "
    "deploy instead.",
)

_EMPTY_NOTE = (
    "No error groups in this window. Error Reporting only sees exceptions that "
    "reach it — a service logging errors as plain text without a stack trace, "
    "or one that never enabled the API, reports nothing here. Fall back to "
    "gcp_logging_query with severity=ERROR."
)


#: How each ``order`` value re-ranks a merged multi-project result. Timestamps
#: are RFC 3339 and therefore sort correctly as strings.
_RANKING_KEYS: dict[str, str] = {
    "count": "count",
    "last_seen": "last_seen",
    "first_seen": "first_seen",
    "affected_users": "affected_users",
}


def _ranking_key(ranking: str) -> Any:
    """Return a sort key matching the ranking the API was asked for."""
    field = _RANKING_KEYS[ranking]

    def key(item: dict[str, Any]) -> Any:
        value = item.get(field)
        return value if isinstance(value, int) else str(value or "")

    return key


def _fetch(
    service: Any, project: str, period: str, order: str, page_size: int, service_filter: str
) -> dict[str, Any]:
    """Run one ``groupStats.list`` call for a project.

    The REST query params are ``timeRange.period`` and
    ``serviceFilter.service``, but ``googleapiclient`` builds its Python
    signatures by replacing the dot with an underscore. Passing the dotted
    names raises ``TypeError: Got an unexpected keyword argument`` before any
    request goes out — the same convention the monitoring tool follows with
    ``interval_startTime``.
    """
    request = service.projects().groupStats()
    payload: dict[str, Any] = request.list(
        projectName=f"projects/{project}",
        pageSize=page_size,
        order=order,
        timeRange_period=period,
        serviceFilter_service=service_filter or None,
    ).execute()
    return payload


@tool(
    name="gcp_error_reporting_top_errors",
    display_name="Error Reporting groups",
    source="gcp",
    description=(
        "List the top error groups from Google Cloud Error Reporting across "
        "the configured projects, ranked by occurrence count, users affected, "
        "or recency. Each group carries its count, when it was first and last "
        "seen, the service and version that raised it, and a representative "
        "stack trace. Errors are already deduplicated server-side."
    ),
    use_cases=[
        "Finding what is throwing right now, without grepping logs",
        "Separating a regression that started during the incident from long-standing noise",
        "Getting a representative stack trace for the most frequent error",
        "Identifying which service and version an exception comes from",
        "Checking how many users an error affects, not just how often it fires",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
            "period": {
                "type": "string",
                "description": "Look-back window.",
                "enum": list(PERIODS),
                "default": _DEFAULT_PERIOD,
            },
            "order": {
                "type": "string",
                "description": (
                    "Ranking. 'first_seen' surfaces newly appeared groups, which "
                    "is what identifies a regression."
                ),
                "enum": list(ORDERS),
                "default": _DEFAULT_ORDER,
            },
            "service": {
                "type": "string",
                "description": (
                    "Return only groups raised by this service name, as reported "
                    "in the error's service context."
                ),
            },
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_error_reporting_top_errors(
    project: str = "",
    period: str = _DEFAULT_PERIOD,
    order: str = _DEFAULT_ORDER,
    service: str = "",
    limit: int = 20,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List the top Error Reporting groups across the requested projects."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, error_groups=[])

    window = (period or _DEFAULT_PERIOD).strip().lower()
    if window not in PERIODS:
        return tool_unavailable(
            "gcp",
            f"unknown period '{period}'; valid values: {', '.join(PERIODS)}",
            error_groups=[],
        )
    ranking = (order or _DEFAULT_ORDER).strip().lower()
    if ranking not in ORDERS:
        return tool_unavailable(
            "gcp",
            f"unknown order '{order}'; valid values: {', '.join(ORDERS)}",
            error_groups=[],
        )
    page_size = max(1, min(int(limit), 100))
    service_filter = (service or "").strip()

    groups: list[dict[str, Any]] = []
    errors: list[str] = []

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            client = build_service(config, ERROR_REPORTING_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), error_groups=[])
        except Exception as exc:
            # The client itself could not be constructed, so no project in this
            # group is reachable — there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_error_reporting_top_errors",
                source="gcp",
                component=_COMPONENT,
                method="clouderrorreporting.discovery.build",
                extras={"projects": group},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "error_groups": [],
            }

        for target in group:
            try:
                response = _fetch(
                    client, target, PERIODS[window], ORDERS[ranking], page_size, service_filter
                )
            except Exception as exc:
                if api_not_enabled(exc):
                    # The API is off here, so the project reports no errors —
                    # which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_error_reporting_top_errors",
                    source="gcp",
                    component=_COMPONENT,
                    method="clouderrorreporting.projects.groupStats.list",
                    severity="warning",
                    extras={"project": target, "period": window},
                )
                # Per-project rather than fatal: the API is enabled per project
                # and one project without it must not discard the others.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            raw = response.get("errorGroupStats")
            groups.extend(normalize_groups(raw if isinstance(raw, list) else [], target))

    if errors and not groups:
        return {"found": False, "error": errors[0], "projects": projects, "error_groups": []}

    # Each project was ranked independently, so a multi-project query needs one
    # more sort to be a single ranked list rather than N concatenated ones.
    # Re-rank on the same key the API was asked for, or the "top N" that follows
    # would be the top N of whichever project happened to be queried first.
    groups.sort(key=_ranking_key(ranking), reverse=True)
    groups = groups[:page_size]

    result: dict[str, Any] = {
        "found": bool(groups),
        "projects": projects,
        "period": window,
        "order": ranking,
        "group_count": len(groups),
        "total_errors": sum(int(item.get("count", 0)) for item in groups),
        "error_groups": groups,
    }
    if not groups and not errors:
        result["note"] = _EMPTY_NOTE
    if errors:
        result["partial_errors"] = errors
    return result
