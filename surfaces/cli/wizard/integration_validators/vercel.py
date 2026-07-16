"""Client-backed validator for the Vercel integration."""

from __future__ import annotations

from integrations.vercel.client import VercelClient, VercelConfig

from .shared import IntegrationHealthResult


def validate_vercel_integration(*, api_token: str, team_id: str = "") -> IntegrationHealthResult:
    """Validate Vercel credentials by listing accessible projects."""
    if not api_token:
        return IntegrationHealthResult(ok=False, detail="Vercel API token is required.")
    try:
        with VercelClient(VercelConfig(api_token=api_token, team_id=team_id)) as client:
            result = client.list_projects()
        if result.get("success"):
            return IntegrationHealthResult(
                ok=True,
                detail=f"Vercel validated; listed {result.get('total', 0)} project(s).",
            )
        return IntegrationHealthResult(
            ok=False,
            detail=f"Vercel validation failed: {result.get('error', 'unknown error')}",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Vercel validation failed: {err}")
