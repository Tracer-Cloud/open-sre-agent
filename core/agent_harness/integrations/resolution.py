"""Shared integration resolution for agent-harness runtime consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from platform.harness_ports import (
    IntegrationResolutionRequest,
    IntegrationResolutionResult,
    resolve_integrations,
    resolve_integrations_with_metadata,
)

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionStore

__all__ = [
    "IntegrationResolutionRequest",
    "IntegrationResolutionResult",
    "resolve_and_cache_integrations",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
]


def resolve_and_cache_integrations(session: SessionStore) -> dict[str, Any]:
    """Resolve a session's integration configs, using and updating its cache."""
    from core.agent_harness.session import integrations_cache as cache

    cached = session.resolved_integrations_cache
    if cached is not None and (
        cache.has_resolved_integrations(cached) or not cache.has_only_runtime_metadata(cached)
    ):
        return dict(cached)

    resolved = resolve_integrations()
    if resolved:
        session.resolved_integrations_cache = cache.merge_resolved_integrations(cached, resolved)
    return dict(session.resolved_integrations_cache or {})
