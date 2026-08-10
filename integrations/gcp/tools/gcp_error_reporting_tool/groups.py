"""Error Reporting ``ErrorGroupStats`` normalization.

Error Reporting is the one GCP surface that has already done the hard part of
log analysis: it clusters stack traces into groups, counts them, and knows when
each group was first seen. That last field is the useful one during an incident
— a group first seen twenty minutes ago is a regression, and a group first seen
eight months ago is background noise with the same count.

The representative event's ``message`` is a full stack trace and routinely runs
to kilobytes. It is truncated here rather than in the tool entrypoint, because
the budget belongs to the shape, not to the caller.

Kept separate from the tool entrypoint so shape handling is testable without an
API client.
"""

from __future__ import annotations

from typing import Any

#: Characters of the representative stack trace kept per group. The top frames
#: identify the fault; everything below is framework noise, and twenty groups of
#: untruncated traces will not fit in a context window.
MESSAGE_BUDGET = 600

_TRUNCATION_SUFFIX = " …[truncated]"


def _sub_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` when it is an object, otherwise an empty one."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int:
    """Parse an int64 that the API encodes as a string."""
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def truncate_message(message: str) -> str:
    """Keep the leading frames of a stack trace, within :data:`MESSAGE_BUDGET`."""
    text = message.strip()
    if len(text) <= MESSAGE_BUDGET:
        return text
    return text[:MESSAGE_BUDGET].rstrip() + _TRUNCATION_SUFFIX


def _services(stats: dict[str, Any]) -> list[str]:
    """Render the affected services as ``service@version`` strings."""
    raw = stats.get("affectedServices")
    if not isinstance(raw, list):
        return []
    rendered: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        service = str(entry.get("service", "")).strip()
        if not service:
            continue
        version = str(entry.get("version", "")).strip()
        label = f"{service}@{version}" if version else service
        if label not in rendered:
            rendered.append(label)
    return rendered


def _tracking_issues(group: dict[str, Any]) -> list[str]:
    """Return the issue-tracker URLs someone already linked to this group."""
    raw = group.get("trackingIssues")
    if not isinstance(raw, list):
        return []
    return [
        str(issue.get("url", "")).strip()
        for issue in raw
        if isinstance(issue, dict) and issue.get("url")
    ]


def normalize_group(stats: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one error group into the compact shape the agent consumes."""
    group = _sub_object(stats, "group")
    representative = _sub_object(stats, "representative")
    context = _sub_object(representative, "serviceContext")

    normalized: dict[str, Any] = {
        "project": project,
        "group_id": str(group.get("groupId", "")),
        "count": _as_int(stats.get("count")),
        "affected_users": _as_int(stats.get("affectedUsersCount")),
        "first_seen": str(stats.get("firstSeenTime", "")),
        "last_seen": str(stats.get("lastSeenTime", "")),
        "message": truncate_message(str(representative.get("message", ""))),
    }

    service = str(context.get("service", "")).strip()
    if service:
        normalized["service"] = service
    version = str(context.get("version", "")).strip()
    if version:
        normalized["version"] = version
    resource = str(context.get("resourceType", "")).strip()
    if resource:
        normalized["resource_type"] = resource
    services = _services(stats)
    if len(services) > 1:
        # One group spanning several services usually means shared library code,
        # which changes where the fix goes.
        normalized["affected_services"] = services
    status = str(group.get("resolutionStatus", "")).strip()
    if status:
        normalized["resolution_status"] = status
    issues = _tracking_issues(group)
    if issues:
        normalized["tracking_issues"] = issues
    return normalized


def normalize_groups(groups: list[Any], project: str) -> list[dict[str, Any]]:
    """Normalize a listing, skipping anything that is not an object."""
    return [normalize_group(item, project) for item in groups if isinstance(item, dict)]
