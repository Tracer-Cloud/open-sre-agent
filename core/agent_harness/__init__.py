"""Decoupled agent harness.

This package owns the surface-agnostic turn harness around the shared
``core.agent.Agent`` loop. It was extracted out of ``interactive_shell`` so the
same harness can drive the interactive terminal **and** be executed headlessly via a plain API call
(:func:`core.agent_harness.turns.headless_dispatch.dispatch_message_to_headless_agent`).

Hard boundary: nothing under ``agent_harness/`` may import from
``interactive_shell``. The dependency direction is one-way:
``interactive_shell -> agent_harness -> core``. See ``agent_harness/AGENTS.md``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.harness import AgentHarness, HarnessConfig, HarnessStartupResult
    from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
    from core.agent_harness.models.turn_snapshot import (
        AgentRuntimeRequest,
        TurnSnapshot,
        TurnSnapshotSource,
    )
    from core.agent_harness.turns.action_driver import ToolCallingDeps
    from core.agent_harness.turns.action_driver import (
        run_action_agent_turn as execute_action_agent_turn,
    )
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence
    from core.agent_harness.turns.evidence_driver import gather_tool_evidence as gather_evidence
    from core.agent_harness.turns.headless_dispatch import dispatch_message_to_headless_agent
    from core.agent_harness.turns.orchestrator import run_turn, stream_answer

# Public name -> (owning submodule, attribute). Resolved lazily via PEP 562 so
# importing any ``core.agent_harness`` submodule (e.g. ``.session``) does not
# eagerly pull the turn-driver stack (``action_driver -> core.agent``) into the
# import graph. This keeps interactive-shell boot cheap.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentHarness": ("core.agent_harness.harness", "AgentHarness"),
    "HarnessConfig": ("core.agent_harness.harness", "HarnessConfig"),
    "HarnessStartupResult": ("core.agent_harness.harness", "HarnessStartupResult"),
    "ShellTurnResult": ("core.agent_harness.models.turn_results", "ShellTurnResult"),
    "ToolCallingTurnResult": ("core.agent_harness.models.turn_results", "ToolCallingTurnResult"),
    "AgentRuntimeRequest": ("core.agent_harness.models.turn_snapshot", "AgentRuntimeRequest"),
    "TurnSnapshot": ("core.agent_harness.models.turn_snapshot", "TurnSnapshot"),
    "TurnSnapshotSource": ("core.agent_harness.models.turn_snapshot", "TurnSnapshotSource"),
    "ToolCallingDeps": ("core.agent_harness.turns.action_driver", "ToolCallingDeps"),
    "execute_action_agent_turn": (
        "core.agent_harness.turns.action_driver",
        "run_action_agent_turn",
    ),
    "gather_tool_evidence": ("core.agent_harness.turns.evidence_driver", "gather_tool_evidence"),
    "gather_evidence": ("core.agent_harness.turns.evidence_driver", "gather_tool_evidence"),
    "dispatch_message_to_headless_agent": (
        "core.agent_harness.turns.headless_dispatch",
        "dispatch_message_to_headless_agent",
    ),
    "run_turn": ("core.agent_harness.turns.orchestrator", "run_turn"),
    "stream_answer": ("core.agent_harness.turns.orchestrator", "stream_answer"),
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
    "AgentHarness",
    "AgentRuntimeRequest",
    "HarnessConfig",
    "HarnessStartupResult",
    "ShellTurnResult",
    "ToolCallingDeps",
    "ToolCallingTurnResult",
    "TurnSnapshot",
    "TurnSnapshotSource",
    "dispatch_message_to_headless_agent",
    "execute_action_agent_turn",
    "gather_evidence",
    "gather_tool_evidence",
    "run_turn",
    "stream_answer",
]
