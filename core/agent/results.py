"""Neutral turn-result models for the agentic turn engine.

These are surface-agnostic "facts only" records: they describe what a turn did
(actions planned/executed, the assistant response) without any terminal,
session, or analytics coupling. The interactive shell's accounting layer
(:mod:`interactive_shell.runtime.core.turn_accounting`) consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Distinguishes the two zero-count outcomes that need different analytics:
# a normal tool-calling run that completed without planning actions ("completed"),
# versus a run that never produced actions because it failed/overflowed ("not_run").
ToolCallingAccountingStatus = Literal["completed", "not_run"]


@dataclass(frozen=True)
class ToolCallingTurnResult:
    """Facts-only outcome of the action tool-calling phase of a turn."""

    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool
    response_text: str = ""
    accounting_status: ToolCallingAccountingStatus = "completed"


@dataclass(frozen=True)
class ShellTurnResult:
    """Outcome of a full turn: the action phase plus the conversational answer."""

    final_intent: str
    action_result: ToolCallingTurnResult
    assistant_response_text: str = ""
    # Opaque conversational-LLM run record (the shell passes its ``LlmRunInfo``).
    # Kept untyped here so ``agent/`` stays decoupled from the shell's telemetry
    # types; consumers read ``.response_text`` off it.
    llm_run: Any | None = None

    @property
    def answered(self) -> bool:
        """A turn is "answered" exactly when the conversational LLM produced a run."""
        return self.llm_run is not None


__all__ = [
    "ShellTurnResult",
    "ToolCallingAccountingStatus",
    "ToolCallingTurnResult",
]
