"""Pure helpers for the metrics-scope tool — unit-testable without a client.

Cloud Monitoring names a metrics scope and every project inside it by project
*number*, never by project id: *"On input, the resource name can be specified
with the scoping project ID or number. On output, the resource name is
specified with the scoping project number."* A bare number is useless to a
model that then has to pass a project **id** to another GCP tool, so
:func:`project_id_index` resolves them through Cloud Resource Manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from integrations.gcp.project_discovery import MAX_DISCOVERED

#: ``lifecycleState`` Resource Manager reports for a project that still exists.
#: Matches ``project_discovery._ACTIVE`` so the two listings cannot disagree.
_ACTIVE = "ACTIVE"

#: Maximum pages to fetch when building the project id index.
_MAX_PROJECT_PAGES = 5


@dataclass(frozen=True)
class ProjectIdIndex:
    """Result of building a project number to id mapping."""

    mapping: dict[str, str]
    truncated: bool
    #: What stopped the listing, when something did. Carried rather than
    #: swallowed so the caller can tell an expected denial from a genuine fault
    #: — this helper degrades either way, but only one of the two is worth a
    #: Sentry event, and a failure nobody ever sees is a failure nobody fixes.
    error: Exception | None = None


def scope_resource_name(project: str) -> str:
    """Render ``project`` as a ``monitoring/v1`` metrics-scope resource name.

    The discovery document constrains this to
    ``^locations/global/metricsScopes/[^/]+$``, so a ``projects/{p}`` path or a
    leading ``v1/`` is rejected before any request goes out.
    """
    return f"locations/global/metricsScopes/{project}"


def trailing_segment(resource_name: str) -> str:
    """Return the last ``/``-separated segment, or ``""`` for an empty name."""
    if not resource_name:
        return ""
    return resource_name.rstrip("/").split("/")[-1]


def scope_project_number(scope_name: str) -> str:
    """Return the project number a metrics-scope resource name ends with."""
    return trailing_segment(scope_name)


def normalize_monitored(entries: list[Any], scope_number: str) -> list[dict[str, Any]]:
    """Normalize ``MetricsScope.monitoredProjects`` into flat rows.

    ``is_scoping_project`` compares trailing path segments, so it never depends
    on id resolution having succeeded — which is what keeps ``self_scoped``
    correct for a principal that cannot list Resource Manager.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number = trailing_segment(str(entry.get("name", "") or ""))
        rows.append(
            {
                "project_number": number,
                # Filled in by attach_project_ids once the index is built.
                "project_id": "",
                "is_scoping_project": bool(number) and number == scope_number,
                "tombstoned": bool(entry.get("isTombstoned", False)),
            }
        )
    return rows


def normalize_scopes(entries: list[Any]) -> list[dict[str, Any]]:
    """Normalize ``listMetricsScopesByMonitoredProject`` results into flat rows.

    ``is_self`` comes from position, not from a number comparison. The API
    documents it: *"The metrics scope representing the specified monitored
    project will always be the first entry in the response."* Inferring it a
    second way would add a path that can disagree with the contract for no gain.
    """
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        scope_name = str(entry.get("name", "") or "")
        rows.append(
            {
                "scope_name": scope_name,
                "scope_project_number": scope_project_number(scope_name),
                "scope_project_id": "",
                "is_self": index == 0,
            }
        )
    return rows


def project_id_index(resource_manager: Any) -> ProjectIdIndex:
    """Return ``{project_number: project_id}`` for every ACTIVE project.

    Best effort by contract: the caller degrades to blank ids rather than
    failing, because ``resourcemanager.projects.list`` is a permission an
    otherwise perfectly good Monitoring principal often lacks.
    """
    try:
        index: dict[str, str] = {}
        token = None
        page_count = 0

        while page_count < _MAX_PROJECT_PAGES:
            list_kwargs = {"pageSize": MAX_DISCOVERED}
            if token:
                list_kwargs["pageToken"] = token

            response = resource_manager.projects().list(**list_kwargs).execute()

            for entry in response.get("projects") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("lifecycleState", _ACTIVE) != _ACTIVE:
                    continue
                number = str(entry.get("projectNumber", "") or "")
                project_id = str(entry.get("projectId", "") or "")
                if number and project_id:
                    index[number] = project_id

            token = response.get("nextPageToken")
            page_count += 1

            if not token:
                break

        return ProjectIdIndex(mapping=index, truncated=bool(token))
    except Exception as exc:  # noqa: BLE001 — id resolution is decoration, never the answer
        return ProjectIdIndex(mapping={}, truncated=False, error=exc)


def attach_project_ids(
    rows: list[dict[str, Any]],
    index: dict[str, str],
    number_key: str,
    id_key: str,
) -> None:
    """Fill ``id_key`` on each row from ``index``, in place."""
    for row in rows:
        resolved = index.get(str(row.get(number_key, "") or ""))
        if resolved:
            row[id_key] = resolved
