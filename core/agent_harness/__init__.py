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

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.agents.action_agent import ToolCallingDeps
    from core.agent_harness.agents.evidence_agent import gather_tool_evidence
    from core.agent_harness.agents.headless_agent import dispatch_message_to_headless_agent
    from core.agent_harness.agents.turn_orchestrator import answer_cli_agent, run_turn
    from core.agent_harness.harness import AgentHarness, HarnessConfig, HarnessStartupResult
    from core.agent_harness.models.turn_context import (
        AgentRuntimeRequest,
        TurnContext,
        TurnContextSource,
    )
    from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult

# Public name -> owning submodule. Resolved lazily via PEP 562 so importing any
# ``core.agent_harness`` submodule (e.g. ``.session``) does not eagerly pull the
# turn-driver stack (``action_agent -> core.agent``) into the import graph.
_LAZY_EXPORTS: dict[str, str] = {
    "ToolCallingDeps": "core.agent_harness.agents.action_agent",
    "gather_tool_evidence": "core.agent_harness.agents.evidence_agent",
    "dispatch_message_to_headless_agent": "core.agent_harness.agents.headless_agent",
    "answer_cli_agent": "core.agent_harness.agents.turn_orchestrator",
    "run_turn": "core.agent_harness.agents.turn_orchestrator",
    "AgentHarness": "core.agent_harness.harness",
    "HarnessConfig": "core.agent_harness.harness",
    "HarnessStartupResult": "core.agent_harness.harness",
    "AgentRuntimeRequest": "core.agent_harness.models.turn_context",
    "TurnContext": "core.agent_harness.models.turn_context",
    "TurnContextSource": "core.agent_harness.models.turn_context",
    "ShellTurnResult": "core.agent_harness.models.turn_results",
    "ToolCallingTurnResult": "core.agent_harness.models.turn_results",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(_LAZY_EXPORTS)


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
    "answer_cli_agent",
    "gather_tool_evidence",
    "dispatch_message_to_headless_agent",
    "run_turn",
]
