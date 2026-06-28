"""Decoupled agent subsystem.

This package owns the surface-agnostic agentic loop and turn harness, extracted
out of ``interactive_shell`` so the same subsystem can drive the interactive
terminal **and** be executed headlessly via a plain API call
(:func:`core.agent.headless_agent.run_agent_turn`).

Hard boundary: nothing under ``agent/`` may import from ``interactive_shell``.
The dependency direction is one-way: ``interactive_shell -> agent -> core.runtime``.
See ``agent/AGENTS.md``.
"""

from __future__ import annotations

from core.agent.agent import (
    ShellTurnResult,
    ToolCallingDeps,
    ToolCallingTurnResult,
    TurnContext,
    TurnContextSource,
    answer_cli_agent,
    execute_action_agent_turn,
    gather_evidence,
    gather_tool_evidence,
    run_agent_turn,
    run_turn,
)

__all__ = [
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
