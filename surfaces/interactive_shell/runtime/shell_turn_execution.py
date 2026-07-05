"""Compose one interactive-shell turn from its action/gather/answer adapters.

Adapter-only: binds the shell's action-turn (``action_turn``), gather pass
(``integration_tool_gathering``), and answer (``answer_turn``) adapters to the
surface-agnostic ``run_turn`` engine. Each adapter owns its own binding; this
file only composes them and attaches turn accounting. The injection contracts
live in ``turn_seams``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Unpack

from rich.console import Console

from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.ports import OutputSink
from core.agent_harness.session import Session
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_plan import TurnPlan
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.answer_turn import answer_shell_question
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.runtime.turn_seams import (
    AnswerKwargs,
    AnswerShellQuestion,
    GatherEvidence,
    RunActionToolTurn,
)
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder


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
    / ``answer_agent`` (the test injection seams, typed in ``turn_seams``). They are
    bound to the live ``session``/``console`` here and handed to
    :func:`core.agent_harness.turns.orchestrator.run_turn`, which performs
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
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        return _execute(
            t,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=request_exit,
            turn_plan=turn_plan,
            output=resolved_output,
            tool_hooks=tool_hooks,
        )

    def answer_bound(t: str, **kwargs: Unpack[AnswerKwargs]) -> LlmRunInfo | None:
        # run_turn controls which keys are present (it omits tool_observation_on_screen
        # on the plain path); AnswerKwargs types them without forcing presence.
        return _answer(t, session, console, output=resolved_output, **kwargs)

    def gather_bound(
        t: str,
        *,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> str | None:
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return _gather(t, session, console, is_tty=is_tty, resolved_integrations=resolved)

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


__all__ = ["execute_shell_turn"]
