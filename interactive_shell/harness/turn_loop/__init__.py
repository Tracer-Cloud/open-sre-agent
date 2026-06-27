"""Shell-local turn loop contracts and runner."""

from __future__ import annotations

from .loop import run_shell_turn
from .types import (
    AnswerAgent,
    CaptureTerminalTurn,
    ExecuteActions,
    GatherEvidence,
    ObservationSource,
    ShellObservation,
    ShellTurnContext,
    ShellTurnDeps,
    ShellTurnResult,
    TurnHooks,
)

__all__ = [
    "AnswerAgent",
    "CaptureTerminalTurn",
    "ExecuteActions",
    "GatherEvidence",
    "ObservationSource",
    "ShellObservation",
    "ShellTurnContext",
    "ShellTurnDeps",
    "ShellTurnResult",
    "TurnHooks",
    "run_shell_turn",
]
