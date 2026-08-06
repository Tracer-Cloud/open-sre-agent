"""Decoupled agent harness.

Public host API: :class:`~core.agent_harness.harness.AgentSession`
(``chat`` / ``investigate``). This package owns the surface-agnostic turn
harness around the shared ``core.agent.Agent`` loop so the interactive
terminal, gateway, and embedders share one entry.

Hard boundary: nothing under ``agent_harness/`` may import from
``interactive_shell``, ``tools``, or ``integrations``. The dependency direction
is one-way: ``interactive_shell -> agent_harness -> core``. See
``agent_harness/AGENTS.md``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.harness import (
        AgentHarness,
        AgentSession,
        ChatDispatcher,
        HarnessConfig,
        HarnessStartupResult,
        SessionConfig,
        SessionStartupResult,
    )
    from core.agent_harness.investigation_api import InvestigationResult
    from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
    from core.agent_harness.turns.action_driver import ActionTurnRunner, ToolCallingDeps
    from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
    from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence as gather_evidence
    from core.agent_harness.turns.headless_adapters import BufferOutputSink
    from core.agent_harness.turns.headless_dispatch import HeadlessAgent
    from core.agent_harness.turns.orchestrator import run_turn, stream_answer
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
    from core.agent_harness.turns.turn_snapshot import (
        AgentRuntimeRequest,
        TurnSnapshot,
        TurnSnapshotSource,
    )

# Public name -> (owning submodule, attribute). Resolved lazily via PEP 562 so
# importing any ``core.agent_harness`` submodule (e.g. ``.session``) does not
# eagerly pull the turn-driver stack (``action_driver -> core.agent``) into the
# import graph. This keeps interactive-shell boot cheap.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentSession": ("core.agent_harness.harness", "AgentSession"),
    "AgentHarness": ("core.agent_harness.harness", "AgentHarness"),
    "SessionConfig": ("core.agent_harness.harness", "SessionConfig"),
    "HarnessConfig": ("core.agent_harness.harness", "HarnessConfig"),
    "SessionStartupResult": ("core.agent_harness.harness", "SessionStartupResult"),
    "HarnessStartupResult": ("core.agent_harness.harness", "HarnessStartupResult"),
    "ChatDispatcher": ("core.agent_harness.harness", "ChatDispatcher"),
    "InvestigationResult": ("core.agent_harness.investigation_api", "InvestigationResult"),
    "TurnResult": ("core.agent_harness.turns.turn_results", "TurnResult"),
    "ToolCallingTurnResult": ("core.agent_harness.turns.turn_results", "ToolCallingTurnResult"),
    "AgentRuntimeRequest": ("core.agent_harness.turns.turn_snapshot", "AgentRuntimeRequest"),
    "TurnSnapshot": ("core.agent_harness.turns.turn_snapshot", "TurnSnapshot"),
    "TurnSnapshotSource": ("core.agent_harness.turns.turn_snapshot", "TurnSnapshotSource"),
    "ToolCallingDeps": ("core.agent_harness.turns.action_driver", "ToolCallingDeps"),
    "ActionTurnRunner": ("core.agent_harness.turns.action_driver", "ActionTurnRunner"),
    "gather_tool_evidence": ("core.agent_harness.turns.evidence_driver", "gather_tool_evidence"),
    "gather_evidence": ("core.agent_harness.turns.evidence_driver", "gather_tool_evidence"),
    "HeadlessAgent": (
        "core.agent_harness.turns.headless_dispatch",
        "HeadlessAgent",
    ),
    "build_default_headless_agent": (
        "core.agent_harness.turns.default_headless_agent",
        "build_default_headless_agent",
    ),
    "BufferOutputSink": (
        "core.agent_harness.turns.headless_adapters",
        "BufferOutputSink",
    ),
    "DefaultPromptContextProvider": (
        "core.agent_harness.prompts.grounding",
        "DefaultPromptContextProvider",
    ),
    "run_turn": ("core.agent_harness.turns.orchestrator", "run_turn"),
    "stream_answer": ("core.agent_harness.turns.orchestrator", "stream_answer"),
    "ChatTurnBindings": ("core.agent_harness.turns.chat_api", "ChatTurnBindings"),
    "dispatch_chat_turn": ("core.agent_harness.turns.chat_api", "dispatch_chat_turn"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    return getattr(importlib.import_module(module_path), attr)


def __dir__() -> list[str]:
    return sorted(_LAZY_EXPORTS)


__all__ = [
    "ActionTurnRunner",
    "AgentHarness",
    "AgentRuntimeRequest",
    "AgentSession",
    "BufferOutputSink",
    "ChatDispatcher",
    "ChatTurnBindings",
    "DefaultPromptContextProvider",
    "HarnessConfig",
    "HarnessStartupResult",
    "HeadlessAgent",
    "InvestigationResult",
    "SessionConfig",
    "SessionStartupResult",
    "ToolCallingDeps",
    "ToolCallingTurnResult",
    "TurnResult",
    "TurnSnapshot",
    "TurnSnapshotSource",
    "build_default_headless_agent",
    "dispatch_chat_turn",
    "gather_evidence",
    "gather_tool_evidence",
    "run_turn",
    "stream_answer",
]
