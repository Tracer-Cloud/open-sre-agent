"""Offline status probes for the compact launch banner."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LaunchStatus:
    """Counts displayed beside the OpenSRE mark."""

    skill_count: int
    integration_count: int


def _count_loaded_skills() -> int:
    """Return the number of discovered action-agent skills."""
    try:
        from core.agent_harness.spi.grounding import list_action_skills

        return len(list_action_skills())
    except Exception:
        return 0


def _count_configured_integrations() -> int:
    """Return how many integrations are configured (any health state)."""
    try:
        from integrations.catalog import configured_integration_health

        return len(configured_integration_health())
    except Exception:
        return 0


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        integration_count=_count_configured_integrations(),
    )


__all__ = ["LaunchStatus", "load_launch_status"]
