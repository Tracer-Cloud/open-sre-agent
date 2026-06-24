"""Dagster sensor tick history query tool."""

from typing import Any

from app.integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_sensor_ticks,
)
from app.tools.tool_decorator import tool


@tool(
    name="list_dagster_sensor_ticks",
    description=(
        "Fetch recent tick history for a Dagster sensor. The sensor is "
        "identified by all three SensorSelector coordinates: repository "
        "location name, repository name, and sensor name."
    ),
    source="dagster",
    surfaces=("investigation", "chat"),
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
