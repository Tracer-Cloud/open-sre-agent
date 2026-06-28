"""Shell turn entry adapters for the interactive OpenSRE shell."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.turn_context import TurnContext
from core.agent_harness.turn_orchestrator import answer_cli_agent as run_core_answer_cli_agent
from core.agent_harness.turn_orchestrator import run_turn
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from interactive_shell.agent_shell.adapters import (
    ShellActionDispatch,
    ShellErrorReporter,
    ShellOutputSink,
    ShellPromptContextProvider,
    ShellReasoningClientProvider,
    ShellRunRecordFactory,
)
from interactive_shell.agent_shell.tool_calling import run_tool_calling_turn
from interactive_shell.runtime import ReplSession
from interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from tools.interactive_shell.tool_gathering import gather_tool_evidence

# Dependency seams used by the harness turn-routing tests.
RunToolCallingTurn = Callable[..., ToolCallingTurnResult]
GatherEvidence = Callable[..., "str | None"]
AnswerAgent = Callable[..., "LlmRunInfo | None"]


def answer_cli_agent(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    turn_ctx: TurnContext | None = None,
) -> LlmRunInfo | None:
    """Run one turn of the terminal assistant (guidance only; no investigation run).

    Delegates to :func:`core.agent_harness.turn_orchestrator.answer_cli_agent`, supplying the shell
    adapters (Rich output, grounding caches, reasoning client, telemetry, action
    dispatch).
    """
    return run_core_answer_cli_agent(
        message,
        session,
        ShellOutputSink(console),
        prompts=ShellPromptContextProvider(session),
        reasoning=ShellReasoningClientProvider(console),
        run_factory=ShellRunRecordFactory(session),
        dispatch=ShellActionDispatch(session, console),
        error_reporter=ShellErrorReporter(),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_ctx=turn_ctx,
    )


def handle_message_with_agent(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    execute_actions: RunToolCallingTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerAgent | None = None,
) -> ShellTurnResult:
    """Run one interactive-shell turn through the decoupled three-path engine.

    The action driver, gather pass, and conversational assistant are bound to the
    live ``session``/``console`` here (so injected test doubles keep their
    ``(text, session, console, ...)`` shape) and handed to
    :func:`core.agent_harness.turn_orchestrator.run_turn`, which performs the pure path routing.
    """
    from core.agent_harness.session.compaction import auto_compact_if_needed

    auto_compact_if_needed(session)
    _execute = execute_actions or run_tool_calling_turn
    _gather = gather_evidence or gather_tool_evidence
    _answer = answer_agent or answer_cli_agent
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)

    def execute_bound(
        t: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_ctx: TurnContext | None = None,
    ) -> ToolCallingTurnResult:
        return _execute(
            t, session, console, confirm_fn=confirm_fn, is_tty=is_tty, turn_ctx=turn_ctx
        )

    def answer_bound(t: str, **kwargs: Any) -> LlmRunInfo | None:
        # Pure passthrough so the engine controls the exact call shape: when it
        # omits ``tool_observation_on_screen`` (no evidence gathered) the bound
        # call omits it too, matching the plain conversational path.
        return _answer(t, session, console, **kwargs)

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
    "AnswerAgent",
    "GatherEvidence",
    "RunToolCallingTurn",
    "answer_cli_agent",
    "handle_message_with_agent",
]
