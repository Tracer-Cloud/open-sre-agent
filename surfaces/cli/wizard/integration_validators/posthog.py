"""Client-backed validator for the PostHog integration."""

from __future__ import annotations

from integrations.posthog.config import build_posthog_config
from integrations.posthog.verifier import validate_posthog_config

from .shared import IntegrationHealthResult


def validate_posthog_integration(
    *,
    base_url: str,
    project_id: str,
    personal_api_key: str,
) -> IntegrationHealthResult:
    """Validate PostHog REST connectivity with a project metadata probe."""
    config = build_posthog_config(
        {
            "base_url": base_url,
            "project_id": project_id,
            "personal_api_key": personal_api_key,
        }
    )
    result = validate_posthog_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
