"""Agent runtime: build and run the agent that executes turns.

The headless agent, the default port family it is built on (``DefaultPorts``),
the default tool provider a host configures with its port factories, the
per-turn binding, the action-turn runner, and the gather ports. Importing this loads ``core.agent``;
hosts import it when a turn is dispatched, not at boot.
"""

from __future__ import annotations

from core.agent_harness.ports import TurnBinding
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.default_ports import DefaultPorts
from core.agent_harness.turns.gather_ports import GatherPorts
from core.agent_harness.turns.headless_dispatch import HeadlessAgent

__all__ = [
    "ActionTurnRunner",
    "DefaultPorts",
    "DefaultToolProvider",
    "GatherPorts",
    "HeadlessAgent",
    "TurnBinding",
]
