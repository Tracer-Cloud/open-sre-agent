# ======== from tools/dagster_assets_tool/ ========

"""Dagster assets materialization query tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_assets_with_materialization,
)


def _items_at(output: dict[str, Any], *keys: str) -> list[Any]:
    value: Any = output
    for key in keys:
        if not isinstance(value, dict):
            return []
        value = value.get(key)
    return value if isinstance(value, list) else []


def _count_phrase(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _map_dagster_assets(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    assets = _items_at(output, "data", "assetsOrError", "nodes")
    if assets:
        record_evidence_entry(
            evidence,
            source="list_dagster_assets",
            label="Dagster Assets",
            summary=_count_phrase(len(assets), "asset"),
        )


@tool(
    name="list_dagster_assets",
    description="List Dagster assets and their latest materialization status.",
    source="dagster",
    evidence_mapper=_map_dagster_assets,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_assets(
    endpoint: str,
    api_token: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    """Return assets and the timestamp/status of their most recent materialization."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_assets_with_materialization(config, limit=limit)


# ======== from tools/dagster_run_logs_tool/ ========

"""Dagster run logs query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    get_run_logs,
)


def _map_dagster_run_logs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    events = _items_at(output, "data", "logsForRun", "events")
    if not events:
        return
    summary = output.get("summary")
    failure_count = summary.get("failure_count", 0) if isinstance(summary, dict) else 0
    failure_count = failure_count if isinstance(failure_count, int) else 0
    summary_text = (
        f"{_count_phrase(len(events), 'event')}, {_count_phrase(failure_count, 'step failure')}"
    )
    if isinstance(summary, dict) and (
        summary.get("truncated") is True or summary.get("fetch_error")
    ):
        summary_text += " (partial results; failure count is a lower bound)"
    record_evidence_entry(
        evidence,
        source="get_dagster_run_logs",
        label="Dagster Run Logs",
        summary=summary_text,
    )


@tool(
    name="get_dagster_run_logs",
    description=(
        "Fetch event logs and error details for a specific Dagster run. "
        "IMPORTANT: a single run may contain MULTIPLE step failures if ops "
        "ran in parallel and several failed independently. The response "
        "includes a top-level `summary.failures` list that pre-counts and "
        "pre-classifies each step failure (step_key, exception_class, "
        "cause_message). Always check `summary.failure_count` first; if it "
        "is greater than 1, surface ALL failures in your diagnosis as "
        "distinct root causes, do not pick only one. The underlying "
        "user-code exception lives in `cause_message` (the wrapper is "
        "always a generic DagsterExecutionStepExecutionError). If "
        "`summary.truncated` is true, the run produced more events than "
        "the inspection cap (`summary.events_examined`); treat the "
        "failure_count as a LOWER BOUND and hedge your diagnosis. If "
        "`summary.fetch_error` is set, a mid-pagination error stopped "
        "the fetch early; the failures shown are a partial set."
    ),
    source="dagster",
    evidence_mapper=_map_dagster_run_logs,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def get_dagster_run_logs(
    endpoint: str,
    *,
    api_token: str = "",
    run_id: str,
) -> dict[str, Any]:
    """Return event logs and any failure error message for the given run id."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return get_run_logs(config, run_id=run_id)


# ======== from tools/dagster_runs_tool/ ========

"""Dagster runs query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_runs,
)


def _map_dagster_runs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    runs = _items_at(output, "data", "runsOrError", "results")
    if runs:
        record_evidence_entry(
            evidence,
            source="list_dagster_runs",
            label="Dagster Runs",
            summary=_count_phrase(len(runs), "run"),
        )


@tool(
    name="list_dagster_runs",
    description=(
        "List recent Dagster pipeline/job runs with status and duration. "
        "When the alert specifies a pipeline name (commonly in its "
        "`pipeline`, `alert_name`, or `details.pipeline` field), ALWAYS "
        "pass that as `job_name` to scope results. Dagster instances run "
        "many pipelines and without the filter you get an interleaved mix "
        "from every pipeline that contaminates your evidence. Do not call "
        "this tool multiple times trying different filters; set "
        '`job_name` once and pair it with `status="FAILURE"` for '
        "incident investigations."
    ),
    source="dagster",
    evidence_mapper=_map_dagster_runs,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_runs(
    endpoint: str,
    api_token: str = "",
    limit: int = 25,
    status: str | None = None,
    job_name: str | None = None,
) -> dict[str, Any]:
    """Return summaries of recent Dagster runs from the configured instance."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_runs(config, limit=limit, status=status, job_name=job_name)


# ======== from tools/dagster_schedules_tool/ ========

"""Dagster schedule tick history query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_schedule_ticks,
)


def _map_dagster_schedule_ticks(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    ticks = _items_at(output, "data", "scheduleOrError", "scheduleState", "ticks")
    if ticks:
        record_evidence_entry(
            evidence,
            source="list_dagster_schedule_ticks",
            label="Dagster Schedule Ticks",
            summary=_count_phrase(len(ticks), "schedule tick"),
        )


@tool(
    name="list_dagster_schedule_ticks",
    description=(
        "Fetch recent tick history for a Dagster schedule. The schedule is "
        "identified by all three ScheduleSelector coordinates: repository "
        "location name, repository name, and schedule name."
    ),
    source="dagster",
    evidence_mapper=_map_dagster_schedule_ticks,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_schedule_ticks(
    endpoint: str,
    *,
    api_token: str = "",
    repository_name: str,
    repository_location_name: str,
    schedule_name: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Return the most recent ticks for the named schedule with status and error."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_schedule_ticks(
        config,
        repository_name=repository_name,
        repository_location_name=repository_location_name,
        schedule_name=schedule_name,
        limit=limit,
    )


# ======== from tools/dagster_sensors_tool/ ========

"""Dagster sensor tick history query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_sensor_ticks,
)


def _map_dagster_sensor_ticks(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    ticks = _items_at(output, "data", "sensorOrError", "sensorState", "ticks")
    if ticks:
        record_evidence_entry(
            evidence,
            source="list_dagster_sensor_ticks",
            label="Dagster Sensor Ticks",
            summary=_count_phrase(len(ticks), "sensor tick"),
        )


@tool(
    name="list_dagster_sensor_ticks",
    description=(
        "Fetch recent tick history for a Dagster sensor. The sensor is "
        "identified by all three SensorSelector coordinates: repository "
        "location name, repository name, and sensor name."
    ),
    source="dagster",
    evidence_mapper=_map_dagster_sensor_ticks,
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_sensor_ticks(
    endpoint: str,
    *,
    api_token: str = "",
    repository_name: str,
    repository_location_name: str,
    sensor_name: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Return the most recent ticks for the named sensor with status and error."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_sensor_ticks(
        config,
        repository_name=repository_name,
        repository_location_name=repository_location_name,
        sensor_name=sensor_name,
        limit=limit,
    )
