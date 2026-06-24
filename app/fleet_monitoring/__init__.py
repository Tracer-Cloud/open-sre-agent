"""Local AI agent fleet collectors and analyzers.

Backend support package for the ``monitor-local-agents`` initiative.
The per-PID probe, registry, status heuristic, token meters, and
other collectors live in submodules and feed the ``/fleet``
slash-command dashboard inside the ``opensre`` interactive shell;
UI wiring lives in ``app/cli/interactive_shell/command_registry/``.

This file is required for setuptools' default ``find_packages``
discovery — without it the ``app.fleet_monitoring.*`` subpackages would be
silently omitted from the built wheel.
"""

from app.fleet_monitoring.bus import BusMessage, publish, subscribe
from app.fleet_monitoring.coordination import BranchClaim, BranchClaims
from app.fleet_monitoring.discovery import (
    ProcessRow,
    discover_agents,
    registered_and_discovered_agents,
)
from app.fleet_monitoring.lifecycle import TerminateResult, terminate
from app.fleet_monitoring.quality import LoopDetector
from app.fleet_monitoring.registry import AgentRecord, AgentRegistry

__all__ = [
    "AgentRecord",
    "AgentRegistry",
    "BranchClaim",
    "BranchClaims",
    "BusMessage",
    "LoopDetector",
    "ProcessRow",
    "TerminateResult",
    "discover_agents",
    "registered_and_discovered_agents",
    "publish",
    "subscribe",
    "terminate",
]
