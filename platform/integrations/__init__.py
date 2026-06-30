"""Shared platform integration runtime helpers."""

from platform.integrations.resolution import (
    IntegrationResolutionResult,
    resolve_integrations,
    resolve_integrations_with_metadata,
)

__all__ = [
    "IntegrationResolutionResult",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
]
