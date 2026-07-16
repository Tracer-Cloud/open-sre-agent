"""Client-backed validator for the Sentry integration."""

from __future__ import annotations

from integrations.sentry import build_sentry_config, validate_sentry_config

from .shared import IntegrationHealthResult


def validate_sentry_integration(
    *,
    base_url: str,
    organization_slug: str,
    auth_token: str,
    project_slug: str = "",
) -> IntegrationHealthResult:
    """Validate Sentry connectivity with an organization issues query."""
    config = build_sentry_config(
        {
            "base_url": base_url,
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": project_slug,
        }
    )
    result = validate_sentry_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
