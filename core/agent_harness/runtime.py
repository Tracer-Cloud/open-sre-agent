"""Agent runtime: build and run the agent that executes turns.

The headless agent and its default factory, the action-turn runner and its
dependency struct, and the gather ports. Importing this loads ``core.agent``;
hosts import it when a turn is dispatched, not at boot.
"""

from __future__ import annotations

from core.agent_harness.turns.action_driver import ActionTurnRunner, ToolCallingDeps
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.gather_ports import GatherPorts
from core.agent_harness.turns.headless_dispatch import HeadlessAgent

__all__ = [
    "ActionTurnRunner",
    "GatherPorts",
    "HeadlessAgent",
    "ToolCallingDeps",
    "build_default_headless_agent",
]
