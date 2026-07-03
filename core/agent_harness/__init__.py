"""Decoupled agent harness.

This package owns the surface-agnostic turn harness around the shared
``core.agent.Agent`` loop. It was extracted out of ``interactive_shell`` so the
same harness can drive the interactive terminal **and** be executed headlessly via a plain API call
(:func:`core.agent_harness.agents.headless_agent.dispatch_message_to_headless_agent`).

Hard boundary: nothing under ``agent_harness/`` may import from
``interactive_shell``. The dependency direction is one-way:
``interactive_shell -> agent_harness -> core``. See ``agent_harness/AGENTS.md``.
"""

from __future__ import annotations

from core.agent_harness.agents.action_agent import ToolCallingDeps
from core.agent_harness.agents.action_agent import (
    run_action_agent_turn as execute_action_agent_turn,
)
from core.agent_harness.agents.evidence_agent import gather_tool_evidence
from core.agent_harness.agents.evidence_agent import gather_tool_evidence as gather_evidence
from core.agent_harness.agents.headless_agent import dispatch_message_to_headless_agent
from core.agent_harness.agents.turn_orchestrator import run_turn, stream_answer
from core.agent_harness.harness import AgentHarness, HarnessConfig, HarnessStartupResult
from core.agent_harness.models.turn_context import (
    AgentRuntimeRequest,
    TurnContext,
    TurnContextSource,
)
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult

__all__ = [
    "AgentHarness",
    "AgentRuntimeRequest",
    "HarnessConfig",
    "HarnessStartupResult",
    "ShellTurnResult",
    "ToolCallingDeps",
    "ToolCallingTurnResult",
    "TurnContext",
    "TurnContextSource",
    "execute_action_agent_turn",
    "gather_evidence",
    "gather_tool_evidence",
    "dispatch_message_to_headless_agent",
    "run_turn",
    "stream_answer",
]
