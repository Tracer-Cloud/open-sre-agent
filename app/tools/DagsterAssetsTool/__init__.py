"""Dagster assets materialization query tool."""

from typing import Any

from app.integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_assets_with_materialization,
)
from app.tools.tool_decorator import tool


@tool(
    name="list_dagster_assets",
    description="List Dagster assets and their latest materialization status.",
    source="dagster",
    surfaces=("investigation", "chat"),
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
