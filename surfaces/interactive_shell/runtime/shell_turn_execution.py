"""Compose one interactive-shell turn from its action/gather/answer adapters.

Adapter-only: binds the shell's action-turn (``action_turn``), gather pass
(``integration_tool_gathering``), and answer (``answer_turn``) adapters to the
surface-agnostic ``run_turn`` engine. Each adapter owns its own binding; this
file only composes them and attaches turn accounting.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from rich.console import Console

from core.agent_harness.agents.turn_orchestrator import run_turn
from core.agent_harness.models.turn_context import TurnContext
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.ports import OutputSink
from core.agent_harness.session import Session
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.answer_turn import answer_shell_question
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder


# Dependency seams: tests inject doubles through the explicit parameters below.
# Typed as protocols (not bare Callable[..., X]) so an injected double must match
# the exact call shape execute_shell_turn drives — a mis-shaped double fails at
# type-check, not at runtime.
class RunActionToolTurn(Protocol):
    """Action-selection seam: run one action turn and return its facts."""

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = ...,
        is_tty: bool | None = ...,
        request_exit: Callable[[], None] | None = ...,
        turn_ctx: TurnContext | None = ...,
        output: OutputSink | None = ...,
        tool_hooks: ToolExecutionHooks | None = ...,
    ) -> ToolCallingTurnResult: ...


class GatherEvidence(Protocol):
    """Gather seam: collect read-only integration evidence, or None."""

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        is_tty: bool | None = ...,
    ) -> str | None: ...


class AnswerShellQuestion(Protocol):
    """Answer seam: respond via the grounded conversational assistant."""

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = ...,
        is_tty: bool | None = ...,
        tool_observation: str | None = ...,
        tool_observation_on_screen: bool = ...,
        turn_ctx: TurnContext | None = ...,
        output: OutputSink | None = ...,
    ) -> LlmRunInfo | None: ...


def execute_shell_turn(
    text: str,
    session: Session,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    execute_actions: RunActionToolTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerShellQuestion | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ShellTurnResult:
    """Execute one submitted interactive-shell turn.

    The action driver, gather pass, and conversational assistant default to the
    shell adapters but are overridable via ``execute_actions`` / ``gather_evidence``
    / ``answer_agent`` (the test injection seams). They are bound to the live
    ``session``/``console`` here and handed to
    :func:`core.agent_harness.agents.turn_orchestrator.run_turn`, which performs
    the pure path routing.
    """
    _execute = execute_actions or run_action_tool_turn
    _gather = gather_evidence or gather_integration_tool_evidence
    _answer = answer_agent or answer_shell_question
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)
    resolved_output = resolve_output_sink(console, output)

    def execute_bound(
        t: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_ctx: TurnContext | None = None,
    ) -> ToolCallingTurnResult:
        return _execute(
            t,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=request_exit,
            turn_ctx=turn_ctx,
            output=resolved_output,
            tool_hooks=tool_hooks,
        )

    def answer_bound(t: str, **kwargs: Any) -> LlmRunInfo | None:
        # Pure passthrough so the engine controls the exact call shape: when it
        # omits ``tool_observation_on_screen`` (no evidence gathered) the bound
        # call omits it too, matching the plain conversational path.
        return _answer(
            t,
            session,
            console,
            output=resolved_output,
            **kwargs,
        )

    def gather_bound(t: str, *, is_tty: bool | None = None) -> str | None:
        return _gather(t, session, console, is_tty=is_tty)

    return run_turn(
        text,
        session,
        execute_actions=execute_bound,
        answer=answer_bound,
        gather=gather_bound,
        accounting=accounting,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


__all__ = [
    "AnswerShellQuestion",
    "GatherEvidence",
    "RunActionToolTurn",
    "execute_shell_turn",
]
