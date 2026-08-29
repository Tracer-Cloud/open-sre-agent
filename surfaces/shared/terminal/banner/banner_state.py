"""Offline status probes for the compact launch banner."""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.paths import REPO_ROOT
from surfaces.shared.terminal.tables import MCP_INTEGRATION_SERVICES


@dataclass(frozen=True)
class LaunchStatus:
    """Counts and availability flags displayed beside the OpenSRE mark."""

    skill_count: int
    mcp_count: int
    mcps_ready: bool
    agents_md_available: bool


def _count_loaded_skills() -> int:
    """Return the number of discovered action-agent skills."""
    try:
        from core.agent_harness.spi.grounding import list_action_skills

        return len(list_action_skills())
    except Exception:
        return 0


def _mcp_health() -> tuple[int, bool]:
    """Return the configured MCP count and whether every entry is usable."""
    try:
        from integrations.catalog import configured_integration_health

        entries = [
            status
            for service, status in configured_integration_health()
            if service in MCP_INTEGRATION_SERVICES
        ]
    except Exception:
        return 0, False
    return len(entries), bool(entries) and all(status == "ok" for status in entries)


def _has_agents_md() -> bool:
    """Return whether the repository instructions used for grounding exist."""
    try:
        return (REPO_ROOT / "AGENTS.md").is_file()
    except OSError:
        return False


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    mcp_count, mcps_ready = _mcp_health()
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        mcp_count=mcp_count,
        mcps_ready=mcps_ready,
        agents_md_available=_has_agents_md(),
    )


__all__ = ["LaunchStatus", "load_launch_status"]
