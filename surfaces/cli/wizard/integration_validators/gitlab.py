"""Client-backed validator for the GitLab integration."""

from __future__ import annotations

from integrations.gitlab import build_gitlab_config, validate_gitlab_config

from .shared import IntegrationHealthResult


def validate_gitlab_integration(
    *,
    base_url: str,
    auth_token: str,
) -> IntegrationHealthResult:
    """Validate Gitlab connectivity with an users api."""
    config = build_gitlab_config({"base_url": base_url, "auth_token": auth_token})
    result = validate_gitlab_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
