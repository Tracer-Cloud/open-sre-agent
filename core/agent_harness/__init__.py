"""Decoupled agent harness.

Public host API: :class:`~core.agent_harness.harness.AgentSession`
(``chat`` / ``investigate``). This package owns the surface-agnostic turn
harness around the shared ``core.agent.Agent`` loop so the interactive
terminal, gateway, and embedders share one entry.

Hard boundary: nothing under ``agent_harness/`` may import from
``interactive_shell``, ``tools``, or ``integrations``. The dependency direction
is one-way: ``interactive_shell -> agent_harness -> core``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
    from core.agent_harness.harness import (
        AgentSession,
        ChatDispatcher,
        SessionConfig,
        SessionStartupResult,
    )
    from core.agent_harness.investigation_api import InvestigationResult
    from core.agent_harness.ports import OutputSink
    from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
    from core.agent_harness.session import SessionCore, SessionManager
    from core.agent_harness.session.integration_resolution import has_resolved_integrations
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.session_goal.progress import (
        format_session_goal_progress,
        format_session_goal_status_line,
    )
    from core.agent_harness.session_goal.run_until import run_until_session_goal
    from core.agent_harness.turns.action_driver import ActionTurnRunner, ToolCallingDeps
    from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
    from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence as gather_evidence
    from core.agent_harness.turns.gather_ports import GatherPorts
    from core.agent_harness.turns.headless_adapters import BufferOutputSink
    from core.agent_harness.turns.headless_dispatch import HeadlessAgent
    from core.agent_harness.turns.host_cancel import ensure_turn_cancel
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
    "SessionConfig": ("core.agent_harness.harness", "SessionConfig"),
    "SessionStartupResult": ("core.agent_harness.harness", "SessionStartupResult"),
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
    # Host-facing session and goal surface (gateway and embedders).
    "SessionCore": ("core.agent_harness.session", "SessionCore"),
    "SessionManager": ("core.agent_harness.session", "SessionManager"),
    "has_resolved_integrations": (
        "core.agent_harness.session.integration_resolution",
        "has_resolved_integrations",
    ),
    "SessionGoal": ("core.agent_harness.session_goal.goal", "SessionGoal"),
    "run_until_session_goal": (
        "core.agent_harness.session_goal.run_until",
        "run_until_session_goal",
    ),
    "format_session_goal_progress": (
        "core.agent_harness.session_goal.progress",
        "format_session_goal_progress",
    ),
    "format_session_goal_status_line": (
        "core.agent_harness.session_goal.progress",
        "format_session_goal_status_line",
    ),
    "DefaultTurnAccounting": (
        "core.agent_harness.accounting.turn_accounting",
        "DefaultTurnAccounting",
    ),
    "GatherPorts": ("core.agent_harness.turns.gather_ports", "GatherPorts"),
    "ensure_turn_cancel": ("core.agent_harness.turns.host_cancel", "ensure_turn_cancel"),
    "OutputSink": ("core.agent_harness.ports", "OutputSink"),
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
    "AgentRuntimeRequest",
    "AgentSession",
    "BufferOutputSink",
    "ChatDispatcher",
    "ChatTurnBindings",
    "DefaultPromptContextProvider",
    "DefaultTurnAccounting",
    "GatherPorts",
    "HeadlessAgent",
    "InvestigationResult",
    "OutputSink",
    "SessionConfig",
    "SessionCore",
    "SessionGoal",
    "SessionManager",
    "SessionStartupResult",
    "ToolCallingDeps",
    "ToolCallingTurnResult",
    "TurnResult",
    "TurnSnapshot",
    "TurnSnapshotSource",
    "build_default_headless_agent",
    "dispatch_chat_turn",
    "ensure_turn_cancel",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "gather_evidence",
    "gather_tool_evidence",
    "has_resolved_integrations",
    "run_turn",
    "run_until_session_goal",
    "stream_answer",
]
