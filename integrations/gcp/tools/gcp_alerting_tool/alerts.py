"""Alert-instance normalization and client-side filtering.

Filtering is deliberately client-side. The ``alerts.list`` ``filter`` parameter
is documented as *"An alert is returned if there is a match on any fields
belonging to the alert or its subfields"* — loose enough that a wrong
expression returns silently empty rather than a 400, and a silently-empty alert
list is exactly the failure this tool exists to fix. One always-correct path
that can be tested offline beats a server round trip that can lie.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _trailing(resource_name: str) -> str:
    """Return the last ``/``-separated segment of a resource name."""
    if not resource_name:
        return ""
    return resource_name.rstrip("/").split("/")[-1]


def _segment(resource_name: str, index: int) -> str:
    """Return path segment ``index``, or ``""`` when the name is shorter."""
    parts = [part for part in resource_name.split("/") if part]
    return parts[index] if len(parts) > index else ""


def normalize_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``Alert`` into the shape the model reads.

    ``resource_project_id`` is the field that places an alert: the resource
    labels are preserved from the condition that fired, so in a split estate
    they name the project the workload actually runs in — which is not the
    project that was queried.
    """
    policy = alert.get("policy") or {}
    policy_name = str(policy.get("name", "") or "")

    resource = alert.get("resource") or {}
    resource_labels = resource.get("labels") or {}

    metric = alert.get("metric") or {}
    log = alert.get("log") or {}

    return {
        "id": _trailing(str(alert.get("name", "") or "")),
        "state": str(alert.get("state", "") or ""),
        "opened_at": str(alert.get("openTime", "") or ""),
        "closed_at": str(alert.get("closeTime", "") or ""),
        "policy": str(policy.get("displayName", "") or ""),
        "policy_id": _trailing(policy_name),
        "policy_project": _segment(policy_name, 1),
        "severity": str(policy.get("severity", "") or ""),
        "metric_type": str(metric.get("type", "") or ""),
        "metric_labels": metric.get("labels") or {},
        "resource_type": str(resource.get("type", "") or ""),
        "resource_labels": resource_labels,
        "resource_project_id": str(resource_labels.get("project_id", "") or ""),
        "log_labels": log.get("extractedLabels") or {},
        "user_labels": policy.get("userLabels") or {},
    }


def parse_timestamp(value: str) -> datetime | None:
    """Parse an RFC 3339 timestamp, returning ``None`` on anything unexpected.

    Returning ``None`` rather than raising is what lets the window filter keep
    an alert it cannot date instead of discarding evidence.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def keep_for_state(alert: dict[str, Any], state: str) -> bool:
    """Whether ``alert`` matches the requested state. ``any`` matches all."""
    if state == "any":
        return True
    return str(alert.get("state", "") or "") == state.upper()


def keep_for_window(alert: dict[str, Any], start: datetime) -> bool:
    """Whether ``alert`` falls inside the lookback window starting at ``start``.

    In window means: still open, or closed inside it, or opened inside it.
    """
    closed_raw = str(alert.get("closed_at", "") or "").strip()
    opened_raw = str(alert.get("opened_at", "") or "").strip()
    closed = parse_timestamp(closed_raw)
    opened = parse_timestamp(opened_raw)

    # A timestamp that is present but will not parse must not cost us the
    # alert — never silently drop evidence over an unexpected shape. Checked
    # before the still-open branch so that neither clause can mask the other:
    # each guard has to earn its own keep.
    if (closed_raw and closed is None) or (opened_raw and opened is None):
        return True

    # Still open, so it is firing now and belongs in every window. The alert
    # that has been up since the incident began is the one a naive lookback
    # drops first, and it is the one the responder most needs.
    if not closed_raw:
        return True

    if closed is not None and closed >= start:
        return True
    return opened is not None and opened >= start


def keep_for_name(alert: dict[str, Any], needle: str) -> bool:
    """Case-insensitive substring match against the policy display name."""
    if not needle:
        return True
    return needle.casefold() in str(alert.get("policy", "") or "").casefold()


def select_alerts(
    alerts: list[dict[str, Any]],
    state: str,
    hours: float,
    name_contains: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply state, window and name filters in order. Does not truncate."""
    start = (now or datetime.now(UTC)) - timedelta(hours=hours)
    return [
        alert
        for alert in alerts
        if keep_for_state(alert, state)
        and keep_for_window(alert, start)
        and keep_for_name(alert, name_contains)
    ]


def runtime_project_ids(alerts: list[dict[str, Any]]) -> list[str]:
    """Distinct non-empty ``resource_project_id`` values, first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for alert in alerts:
        project_id = str(alert.get("resource_project_id", "") or "")
        if project_id and project_id not in seen:
            seen.add(project_id)
            ordered.append(project_id)
    return ordered
