"""Agent runtime: build and run the agent that executes turns.

The headless agent, the two HeadlessAgent builds it is built on (``DefaultHeadlessBuild``,
``InMemoryHeadlessBuild``),
the default tool provider a host configures with its tool factories, the
per-turn binding, the turn plan the runtime hands to stages, the action-turn
runner, the gather phase and report budget, and the ReAct engine itself
(``AgentConfig``/``build_agent`` and the default LLM factories) that the
investigation pipeline shares. Importing this loads ``core.agent``;
hosts import it when a turn is dispatched, not at boot.
"""

from __future__ import annotations

from core.agent_harness.agent_build_config import AgentBuildConfig, DescribeTool
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.llm_resolution import (
    agent_llm_is_cli_backed,
    default_llm_factory,
    default_reasoning_llm_factory,
)
from core.agent_harness.ports import TurnBinding
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.gather_phase import MAX_REPORT_GATHER_ITERATIONS, GatherPhase
from core.agent_harness.turns.headless_agent import AgentBusyError, HeadlessAgent
from core.agent_harness.turns.headless_build import DefaultHeadlessBuild, InMemoryHeadlessBuild
from core.agent_harness.turns.turn_plan import TurnPlan

__all__ = [
    "MAX_REPORT_GATHER_ITERATIONS",
    "ActionTurnRunner",
    "AgentBuildConfig",
    "AgentBusyError",
    "AgentConfig",
    "DefaultHeadlessBuild",
    "DescribeTool",
    "DefaultToolProvider",
    "GatherPhase",
    "HeadlessAgent",
    "InMemoryHeadlessBuild",
    "TurnBinding",
    "TurnPlan",
    "agent_llm_is_cli_backed",
    "build_agent",
    "default_llm_factory",
    "default_reasoning_llm_factory",
]
