"""Client-backed validator for the Dagster integration."""

from __future__ import annotations

from integrations.dagster import build_dagster_config, validate_dagster_config

from .shared import IntegrationHealthResult


def validate_dagster_integration(
    *,
    endpoint: str,
    api_token: str = "",
) -> IntegrationHealthResult:
    """Validate Dagster connectivity via a GraphQL version probe."""
    config = build_dagster_config({"endpoint": endpoint, "api_token": api_token})
    result = validate_dagster_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
