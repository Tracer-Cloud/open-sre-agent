"""Shared platform integration runtime helpers."""

from platform.integrations.resolution import (
    IntegrationResolutionRequest,
    IntegrationResolutionResult,
    resolve_integrations,
    resolve_integrations_with_metadata,
)

__all__ = [
    "IntegrationResolutionRequest",
    "IntegrationResolutionResult",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
]
