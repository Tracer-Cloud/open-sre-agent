"""Public semantic entrypoint for the core agent subsystem."""

from __future__ import annotations

from core.agent.action_agent import ToolCallingDeps
from core.agent.action_agent import run_agent_turn as execute_action_agent_turn
from core.agent.evidence_agent import gather_tool_evidence
from core.agent.evidence_agent import gather_tool_evidence as gather_evidence
from core.agent.headless_agent import run_agent_turn
from core.agent.turn_context import AgentRuntimeRequest, TurnContext, TurnContextSource
from core.agent.turn_orchestrator import answer_cli_agent, run_turn
from core.agent.turn_results import ShellTurnResult, ToolCallingTurnResult

__all__ = [
    "AgentRuntimeRequest",
    "ShellTurnResult",
    "ToolCallingDeps",
    "ToolCallingTurnResult",
    "TurnContext",
    "TurnContextSource",
    "answer_cli_agent",
    "execute_action_agent_turn",
    "gather_evidence",
    "gather_tool_evidence",
    "run_agent_turn",
    "run_turn",
]
