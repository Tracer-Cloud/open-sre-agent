"""Per-turn integration snapshots for analytics capture."""

from __future__ import annotations

from typing import Any, Protocol

from core.domain.alerts.alert_source import SECONDARY_TOOL_SOURCES
from core.orchestration.node.investigate.tools import get_available_tools
from integrations.registry import family_key


class _IntegrationSession(Protocol):
    configured_integrations: tuple[str, ...]
    configured_integrations_known: bool
    resolved_integrations_cache: dict[str, Any] | None


def build_turn_integration_snapshot(session: _IntegrationSession | None) -> dict[str, Any]:
    """Return analytics-friendly integration state for one LLM generation turn."""
    configured = _configured_slugs(session)
    resolved = _resolved_integrations(session)
    connected = _connected_slugs(configured, resolved)
    return {
        "connected_integrations": connected,
        "connected_integrations_count": len(connected),
        "configured_integrations": configured,
        "integration_snapshot_source": "runtime_config",
    }


def _configured_slugs(session: _IntegrationSession | None) -> list[str]:
    if session is not None and session.configured_integrations_known:
        return sorted(session.configured_integrations)
    try:
        from integrations.verify import resolve_effective_integrations

        return sorted(resolve_effective_integrations())
    except Exception:
        return []


def _resolved_integrations(session: _IntegrationSession | None) -> dict[str, Any]:
    if session is not None and session.resolved_integrations_cache is not None:
        return session.resolved_integrations_cache
    try:
        from core.orchestration.node.resolve_integrations import resolve_integrations_quiet

        return resolve_integrations_quiet({})  # type: ignore[arg-type]
    except Exception:
        return {}


def _connected_slugs(configured: list[str], resolved: dict[str, Any]) -> list[str]:
    if not configured or not resolved:
        return []
    try:
        tools = get_available_tools(resolved)
        active_families = {
            family_key(str(tool.source))
            for tool in tools
            if str(tool.source) not in SECONDARY_TOOL_SOURCES
        }
        if not active_families:
            return []
        return sorted(svc for svc in configured if family_key(svc) in active_families)
    except Exception:
        return []
