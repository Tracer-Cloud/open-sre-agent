"""Client-backed validator for the Jenkins integration."""

from __future__ import annotations

from integrations.jenkins import build_jenkins_config, validate_jenkins_config

from .shared import IntegrationHealthResult


def validate_jenkins_integration(
    *,
    base_url: str,
    username: str,
    api_token: str,
) -> IntegrationHealthResult:
    """Validate Jenkins connectivity with a server-info query."""
    config = build_jenkins_config(
        {"base_url": base_url, "username": username, "api_token": api_token}
    )
    result = validate_jenkins_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
