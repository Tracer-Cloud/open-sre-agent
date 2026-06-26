"""Dagster schedule tick history query tool."""

from typing import Any

from integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_schedule_ticks,
)
from tools.tool_decorator import tool


@tool(
    name="list_dagster_schedule_ticks",
    description=(
        "Fetch recent tick history for a Dagster schedule. The schedule is "
        "identified by all three ScheduleSelector coordinates: repository "
        "location name, repository name, and schedule name."
    ),
    source="dagster",
    surfaces=("investigation", "chat"),
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
