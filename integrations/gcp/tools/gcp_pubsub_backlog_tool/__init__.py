"""Pub/Sub subscription backlog — whether consumers are keeping up.

An asynchronous pipeline fails quietly. Publishers keep succeeding, the API
stays green, and the only evidence that a consumer died is a backlog that grows
and an oldest-message age that climbs. This tool reports both alongside the
subscription configuration that explains them — a missing dead-letter topic, a
push endpoint that is 5xx-ing, a detached subscription receiving nothing.

Two APIs are needed because Pub/Sub splits the answer: ``pubsub/v1`` has the
configuration and ``monitoring/v3`` has the backlog. The join is in
:mod:`.backlog`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    MONITORING_API,
    PUBSUB_API,
    GCPClientError,
    api_not_enabled,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects, resolve_projects
from integrations.gcp.tool_params import PROJECT_PROPERTY, config_from, gcp_tool_params
from integrations.gcp.tools.gcp_pubsub_backlog_tool.backlog import (
    OLDEST_AGE_METRIC,
    UNDELIVERED_METRIC,
    attach_backlog,
    latest_by_subscription,
    normalize_subscriptions,
)

_COMPONENT = "integrations.gcp.tools.gcp_pubsub_backlog_tool"

#: Look-back for the backlog metrics. Only the newest point is used; the window
#: exists so a subscription whose metric is written every minute still has one.
_METRIC_WINDOW_MINUTES = 10

#: Alignment period, in seconds. Both metrics are gauges, so the last value in
#: the window is the current one.
_ALIGNMENT_SECONDS = 60

_ANTI_EXAMPLES = (
    "Do not use this to read message payloads — it reports queue depth and "
    "subscription configuration, never message contents.",
    "Do not treat a large backlog alone as the fault. A batch subscription runs "
    "one deep by design; the rising oldest_unacked_age_seconds is the signal.",
)

_STALLED_NOTE = (
    "One or more subscriptions have messages older than five minutes un-acked. "
    "Check the consumer for that subscription first: for a pull subscription "
    "the consumers are gone or crash-looping, and for a push subscription the "
    "endpoint is failing. A subscription with no dead_letter_topic retries a "
    "poison message forever, so the backlog will not drain on its own."
)

_NO_METRIC_NOTE = (
    "Backlog metrics were unavailable for one or more subscriptions "
    "(backlog_unknown). The principal needs roles/monitoring.viewer in addition "
    "to roles/pubsub.viewer; without it only configuration is reported."
)


def _list_subscriptions(service: Any, project: str, page_size: int) -> dict[str, Any]:
    """Run one ``projects.subscriptions.list`` call."""
    payload: dict[str, Any] = (
        service.projects()
        .subscriptions()
        .list(project=f"projects/{project}", pageSize=page_size)
        .execute()
    )
    return payload


def _read_metric(service: Any, project: str, metric_type: str, page_size: int) -> dict[str, Any]:
    """Read the most recent points of one backlog metric across a project."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=_METRIC_WINDOW_MINUTES)
    payload: dict[str, Any] = (
        service.projects()
        .timeSeries()
        .list(
            name=f"projects/{project}",
            filter=f'metric.type = "{metric_type}"',
            interval_startTime=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            interval_endTime=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            aggregation_alignmentPeriod=f"{_ALIGNMENT_SECONDS}s",
            aggregation_perSeriesAligner="ALIGN_MAX",
            pageSize=page_size,
        )
        .execute()
    )
    return payload


def _backlog_for(
    monitoring: Any, project: str, page_size: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(undelivered, oldest_age)`` maps keyed by subscription id.

    A monitoring failure is swallowed into empty maps rather than raised: the
    subscription configuration is still worth reporting, and the caller marks
    the affected rows ``backlog_unknown``.
    """
    results: list[dict[str, Any]] = []
    for metric_type in (UNDELIVERED_METRIC, OLDEST_AGE_METRIC):
        try:
            response = _read_metric(monitoring, project, metric_type, page_size)
        except Exception:
            results.append({})
            continue
        series = response.get("timeSeries")
        results.append(latest_by_subscription(series if isinstance(series, list) else []))
    return results[0], results[1]


@tool(
    name="gcp_pubsub_backlog",
    display_name="Pub/Sub backlog",
    source="gcp",
    description=(
        "List Pub/Sub subscriptions across the configured GCP projects with "
        "their current backlog: undelivered message count and the age of the "
        "oldest un-acknowledged message, joined to the subscription's topic, "
        "delivery mode, push endpoint, dead-letter policy and retry backoff."
    ),
    use_cases=[
        "Checking whether a consumer has stopped acknowledging messages",
        "Finding which subscription is behind a growing processing delay",
        "Seeing whether a push subscription's endpoint is failing",
        "Spotting a subscription with no dead-letter topic that cannot drain a poison message",
        "Confirming an asynchronous pipeline is healthy when the API looks fine",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "project": PROJECT_PROPERTY,
            "name_contains": {
                "type": "string",
                "description": (
                    "Return only subscriptions whose name or topic contains this substring."
                ),
            },
            "backlogged_only": {
                "type": "boolean",
                "description": (
                    "Return only subscriptions with undelivered messages or a "
                    "stalled oldest message."
                ),
                "default": False,
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_pubsub_backlog(
    project: str = "",
    name_contains: str = "",
    backlogged_only: bool = False,
    limit: int = 100,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List Pub/Sub subscriptions with their backlog across the requested projects."""
    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, subscriptions=[])

    needle = (name_contains or "").strip().lower()
    page_size = max(1, min(int(limit), 500))

    subscriptions: list[dict[str, Any]] = []
    errors: list[str] = []
    truncated = False

    for config_payload, group in group_projects(projects, project_configs):
        try:
            config = config_from(config_payload, fallback_project=group[0])
            pubsub = build_service(config, PUBSUB_API)
            monitoring = build_service(config, MONITORING_API)
        except GCPClientError as exc:
            return tool_unavailable("gcp", str(exc), subscriptions=[])
        except Exception as exc:
            # The clients themselves could not be constructed, so no project in
            # this group is reachable — there is nothing to degrade to.
            report_run_error(
                exc,
                tool_name="gcp_pubsub_backlog",
                source="gcp",
                component=_COMPONENT,
                method="pubsub.discovery.build",
                extras={"projects": group},
            )
            return {
                "found": False,
                "error": describe_api_error(exc),
                "projects": projects,
                "subscriptions": [],
            }

        for target in group:
            try:
                response = _list_subscriptions(pubsub, target, page_size)
            except Exception as exc:
                if api_not_enabled(exc):
                    # Pub/Sub is off here, so the project has no subscriptions —
                    # which is the answer, not a failure to get one.
                    continue
                report_run_error(
                    exc,
                    tool_name="gcp_pubsub_backlog",
                    source="gcp",
                    component=_COMPONENT,
                    method="pubsub.projects.subscriptions.list",
                    severity="warning",
                    extras={"project": target},
                )
                # Per-project rather than fatal: Pub/Sub is commonly enabled in
                # only some projects, and one 403 must not discard the others.
                errors.append(f"{target}: {describe_api_error(exc)}")
                continue
            raw = response.get("subscriptions")
            found = normalize_subscriptions(raw if isinstance(raw, list) else [], target)
            truncated = truncated or bool(response.get("nextPageToken"))
            if not found:
                continue
            undelivered, oldest_age = _backlog_for(monitoring, target, page_size)
            subscriptions.extend(attach_backlog(entry, undelivered, oldest_age) for entry in found)

    if errors and not subscriptions:
        return {"found": False, "error": errors[0], "projects": projects, "subscriptions": []}

    if needle:
        subscriptions = [
            item
            for item in subscriptions
            if needle in str(item.get("name", "")).lower()
            or needle in str(item.get("topic", "")).lower()
        ]
    if backlogged_only:
        subscriptions = [
            item
            for item in subscriptions
            if int(item.get("undelivered_messages") or 0) > 0 or item.get("stalled")
        ]
    # Deepest backlog first: on an estate with hundreds of subscriptions that is
    # the order an investigation reads them in.
    subscriptions.sort(
        key=lambda item: (
            -int(item.get("undelivered_messages") or 0),
            str(item.get("project", "")),
            str(item.get("name", "")),
        )
    )

    stalled = [item for item in subscriptions if item.get("stalled")]
    result: dict[str, Any] = {
        "found": bool(subscriptions),
        "projects": projects,
        "subscription_count": len(subscriptions),
        "stalled_count": len(stalled),
        "total_undelivered": sum(
            int(item.get("undelivered_messages") or 0) for item in subscriptions
        ),
        "subscriptions": subscriptions,
        "truncated": truncated,
    }
    notes: list[str] = []
    if stalled:
        notes.append(_STALLED_NOTE)
    if any(item.get("backlog_unknown") for item in subscriptions):
        notes.append(_NO_METRIC_NOTE)
    if notes:
        result["note"] = " ".join(notes)
    if errors:
        result["partial_errors"] = errors
    return result
