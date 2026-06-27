"""Typed contract for one interactive-shell agent turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console

from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder

ObservationSource = Literal["terminal_action", "gather"]

ExecuteActions = Callable[..., TerminalActionExecutionResult]
GatherEvidence = Callable[..., str | None]
AnswerAgent = Callable[..., LlmRunInfo | None]
CaptureTerminalTurn = Callable[..., None]


@dataclass(frozen=True)
class ShellTurnContext:
    text: str
    session: ReplSession
    console: Console
    recorder: PromptRecorder | None
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None


@dataclass(frozen=True)
class ShellTurnDeps:
    execute_actions: ExecuteActions
    gather_evidence: GatherEvidence
    answer_agent: AnswerAgent
    capture_terminal_turn: CaptureTerminalTurn | None = None


@dataclass(frozen=True)
class ShellObservation:
    source: ObservationSource
    text: str
    on_screen: bool


@dataclass(frozen=True)
class ShellTurnResult:
    final_intent: str
    action_result: TerminalActionExecutionResult
    observations: tuple[ShellObservation, ...] = field(default_factory=tuple)
    assistant_response_text: str = ""
    answered: bool = False
    llm_run: LlmRunInfo | None = None


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
]
