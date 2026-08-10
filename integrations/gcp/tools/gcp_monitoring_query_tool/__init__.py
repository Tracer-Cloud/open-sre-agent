"""Cloud Monitoring time-series tool — the CloudWatch metrics analogue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    MONITORING_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import resolve_projects
from integrations.gcp.tool_params import config_from, gcp_tool_params
from integrations.gcp.tools.gcp_monitoring_query_tool.aligners import (
    FALLBACK_ALIGNER,
    aligner_for_kind,
    describes_rejected_aligner,
    descriptor_kind,
    extract_metric_type,
)
from integrations.gcp.tools.gcp_monitoring_query_tool.series import normalize_all

_COMPONENT = "integrations.gcp.tools.gcp_monitoring_query_tool"
_MAX_HOURS = 24 * 30

#: Held as a constant rather than split across two adjacent literals at the
#: call site, where an implicit concatenation reads as a missing comma.
_MISSING_FILTER_MESSAGE = (
    'filter is required, e.g. metric.type="compute.googleapis.com/instance/cpu/utilization"'
)


def _failure(exc: Exception, detail: str, project: str, monitoring_filter: str) -> dict[str, Any]:
    report_run_error(
        exc,
        tool_name="gcp_monitoring_query",
        source="gcp",
        component=_COMPONENT,
        method="monitoring.projects.timeSeries.list",
        extras={"project": project, "filter": monitoring_filter},
    )
    return {
        "found": False,
        "error": detail,
        "project": project,
        "filter": monitoring_filter,
        "series": [],
    }


_ANTI_EXAMPLES = (
    "Do not pass a metric display name; filter needs the metric type, "
    'e.g. metric.type="kubernetes.io/container/cpu/request_utilization".',
    "Do not pass a list or '*' — this tool takes one metrics scope per call. "
    "That is a call-shape limit, not a reach limit: a scoping project's series "
    "already span every project it monitors.",
)


@tool(
    name="gcp_monitoring_query",
    display_name="Cloud Monitoring",
    source="gcp",
    description=(
        "Read Google Cloud Monitoring time series for one metrics scope. Supply a "
        "monitoring filter naming the metric type; returns aligned data points "
        "over the requested window."
    ),
    use_cases=[
        "Checking CPU or memory utilization for a GKE workload during an incident",
        "Reading request count or error rate for a Cloud Run or GCE service",
        "Confirming whether a metric changed before or after a deployment",
    ],
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": (
                    "Cloud Monitoring filter, e.g. "
                    'metric.type="compute.googleapis.com/instance/cpu/utilization"'
                ),
            },
            "project": {
                "type": "string",
                "description": (
                    "Project id to query — a Cloud Monitoring metrics scope. One "
                    "scope per call: unlike gcp_logging_query this takes no list "
                    "and no '*'. A scope returns series for every project it "
                    "monitors, so when metrics are centralised, pass the "
                    "observability project and read resource_labels.project_id "
                    "to see which project each series came from. Omit for the "
                    "default project."
                ),
            },
            "hours": {
                "type": "number",
                "description": "Lookback window in hours (default 1)",
                "default": 1,
            },
            "alignment_period_seconds": {
                "type": "integer",
                "description": "Downsampling bucket in seconds (default 300)",
                "default": 300,
            },
            "aligner": {
                "type": "string",
                "description": (
                    "Optional aggregation aligner, e.g. ALIGN_MAX or "
                    "ALIGN_PERCENTILE_99. Omit to pick one that matches the "
                    "metric kind (mean for gauges, rate for counters)."
                ),
            },
        },
        "required": ["filter"],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_monitoring_query(
    filter: str = "",  # noqa: A002 — matches the Cloud Monitoring API field name
    project: str = "",
    hours: float = 1.0,
    alignment_period_seconds: int = 300,
    aligner: str = "",
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Fetch time series from Cloud Monitoring."""
    if not (filter or "").strip():
        return tool_unavailable(
            "gcp",
            _MISSING_FILTER_MESSAGE,
            series=[],
        )

    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return tool_unavailable("gcp", error, series=[])
    # The v3 list endpoint is scoped to a single project by URL, so a
    # multi-project request cannot be honoured in one call. Be explicit rather
    # than silently reading only the first.
    if len(projects) > 1:
        return tool_unavailable(
            "gcp",
            "gcp_monitoring_query reads one project per call; "
            f"got {len(projects)}. Call it once per project.",
            series=[],
        )

    window = max(0.0, min(float(hours or 1.0), _MAX_HOURS)) or 1.0
    end = datetime.now(UTC)
    start = end - timedelta(hours=window)
    alignment = max(60, int(alignment_period_seconds or 300))

    try:
        config = config_from((project_configs or {}).get(projects[0]), fallback_project=projects[0])
        service = build_service(config, MONITORING_API)
    except GCPClientError as exc:
        return tool_unavailable("gcp", str(exc), series=[])

    chosen = (aligner or "").strip().upper()
    if not chosen:
        chosen = aligner_for_kind(
            descriptor_kind(service, projects[0], extract_metric_type(filter))
        )

    def _fetch(per_series_aligner: str) -> dict[str, Any]:
        # The discovery client is untyped, so annotate what the call returns.
        payload: dict[str, Any] = (
            service.projects()
            .timeSeries()
            .list(
                name=f"projects/{projects[0]}",
                filter=filter,
                interval_startTime=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                interval_endTime=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                aggregation_alignmentPeriod=f"{alignment}s",
                aggregation_perSeriesAligner=per_series_aligner,
                pageSize=max(1, min(int(limit), 500)),
            )
            .execute()
        )
        return payload

    try:
        response = _fetch(chosen)
    except Exception as exc:
        detail = describe_api_error(exc)
        # The descriptor lookup can come back empty (custom metric, or a
        # principal that may read series but not descriptors), leaving a
        # gauge-shaped guess on a counter. One retry with the kind-agnostic
        # aligner turns that into a result instead of a 400 the agent must
        # reason about.
        retry_aligner = FALLBACK_ALIGNER
        if not (aligner or "").strip() and describes_rejected_aligner(detail):
            try:
                response = _fetch(retry_aligner)
            except Exception:
                return _failure(exc, detail, projects[0], filter)
            chosen = retry_aligner
        else:
            return _failure(exc, detail, projects[0], filter)

    series = normalize_all(response.get("timeSeries") or [])
    return {
        "found": bool(series),
        "project": projects[0],
        "filter": filter,
        "window_hours": window,
        "alignment_period_seconds": alignment,
        # Rates are per second; say which aligner produced the numbers so the
        # agent does not read a rate as a raw count.
        "aligner": chosen,
        "series_count": len(series),
        "series": series,
    }
