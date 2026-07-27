"""Client-backed validators for alerting and on-call integrations."""

from __future__ import annotations

from integrations.alertmanager.client import make_alertmanager_client
from integrations.betterstack import build_betterstack_config, validate_betterstack_config
from integrations.opsgenie.client import OpsGenieClient, OpsGenieConfig

from .shared import IntegrationHealthResult


def validate_betterstack_integration(
    *,
    query_endpoint: str,
    username: str,
    password: str,
    sources: list[str] | None = None,
) -> IntegrationHealthResult:
    """Validate Better Stack Telemetry credentials via a ``SELECT 1`` probe."""
    try:
        config = build_betterstack_config(
            {
                "query_endpoint": query_endpoint,
                "username": username,
                "password": password,
                "sources": list(sources or []),
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Better Stack config invalid: {err}")
    result = validate_betterstack_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)


def validate_alertmanager_integration(
    *,
    base_url: str,
    bearer_token: str = "",
    username: str = "",
    password: str = "",
) -> IntegrationHealthResult:
    """Validate Alertmanager connectivity via the /api/v2/status endpoint."""
    if not base_url:
        return IntegrationHealthResult(ok=False, detail="Alertmanager URL is required.")
    client = make_alertmanager_client(
        base_url=base_url,
        bearer_token=bearer_token or None,
        username=username or None,
        password=password or None,
    )
    if client is None:
        return IntegrationHealthResult(ok=False, detail="Invalid Alertmanager URL.")
    try:
        with client:
            result = client.get_status()
        if result.get("success"):
            status_data = result.get("status", {})
            cluster_status = (
                status_data.get("cluster", {}).get("status", "unknown")
                if isinstance(status_data, dict)
                else "ok"
            )
            return IntegrationHealthResult(
                ok=True,
                detail=f"Connected to Alertmanager at {base_url}; cluster status: {cluster_status}.",
            )
        return IntegrationHealthResult(
            ok=False,
            detail=f"Alertmanager validation failed: {result.get('error', 'unknown error')}",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Alertmanager validation failed: {err}")


def validate_opsgenie_integration(
    *,
    api_key: str,
    region: str = "us",
) -> IntegrationHealthResult:
    """Validate OpsGenie connectivity by listing alerts."""
    if not api_key:
        return IntegrationHealthResult(ok=False, detail="OpsGenie API key is required.")
    try:
        config = OpsGenieConfig(api_key=api_key, region=region)
        with OpsGenieClient(config) as client:
            result = client.list_alerts(limit=1)
        if result.get("success"):
            return IntegrationHealthResult(
                ok=True,
                detail=f"OpsGenie validated ({config.region.upper()} region); API key accepted.",
            )
        return IntegrationHealthResult(
            ok=False,
            detail=f"OpsGenie validation failed: {result.get('error', 'unknown error')}",
        )
    except Exception as err:
        return IntegrationHealthResult(
            ok=False,
            detail=f"OpsGenie validation failed: {err}",
        )
