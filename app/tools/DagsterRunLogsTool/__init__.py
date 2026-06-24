"""Dagster run logs query tool."""

from typing import Any

from app.integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    get_run_logs,
)
from app.tools.tool_decorator import tool


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
    surfaces=("investigation", "chat"),
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
