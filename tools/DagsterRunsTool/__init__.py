"""Dagster runs query tool."""

from typing import Any

from integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_runs,
)
from tools.tool_decorator import tool


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
    surfaces=("investigation", "chat"),
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
