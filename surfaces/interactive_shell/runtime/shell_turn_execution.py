"""Compose one interactive-shell turn from its action/gather/answer adapters.

Adapter-only: binds the shell's action-turn (``action_turn``), gather pass
(``integration_tool_gathering``), and answer (``answer_turn``) adapters, then
calls the public host API (:meth:`AgentSession.chat`). Each adapter owns its
own binding; this file only composes them and attaches turn accounting.
The injection contracts live in ``turn_seams``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.ports import AnswerRequest, OutputSink
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.action_turn import ShellActionRunner
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.answer_turn import answer_shell_question
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.runtime.turn_seams import (
    AnswerShellQuestion,
    GatherEvidence,
    RunActionToolTurn,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder


@dataclass(frozen=True)
class _ShellTurnBindings:
    """Session/console-bound seams handed to ``run_turn`` for one shell turn."""

    session: Session
    console: Console
    gather: GatherEvidence
    answer: AnswerShellQuestion
    output: OutputSink
    action_runner: ShellActionRunner | None = None
    execute: RunActionToolTurn | None = None
    request_exit: Callable[[], None] | None = None
    tool_hooks: ToolExecutionHooks | None = None

    def execute_actions(
        self,
        text: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        if self.action_runner is not None:
            return self.action_runner.run(
                text,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                turn_plan=turn_plan,
            )
        if self.execute is None:
            raise RuntimeError("shell turn bindings missing action runner or execute seam")
        return self.execute(
            text,
            self.session,
            self.console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=self.request_exit,
            turn_plan=turn_plan,
            output=self.output,
            tool_hooks=self.tool_hooks,
        )

    def answer_question(self, text: str, request: AnswerRequest) -> LlmRunInfo | None:
        return self.answer(
            text,
            self.session,
            self.console,
            output=self.output,
            request=request,
        )

    def gather_evidence(self, text: str, *, turn_plan: TurnPlan | None = None) -> str | None:
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return self.gather(
            text,
            self.session,
            self.console,
            resolved_integrations=resolved,
        )


@dataclass(frozen=True, slots=True)
class _ShellChatDispatcher:
    """TTY adapter: satisfies :class:`ChatDispatcher` for :meth:`AgentSession.chat`."""

    session: Session
    bindings: ChatTurnBindings

    def dispatch(self, message: str) -> TurnResult:
        return dispatch_chat_turn(message, self.session, self.bindings)


def execute_shell_turn(
    text: str,
    session: Session,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    action_runner: ShellActionRunner | None = None,
    execute_actions: RunActionToolTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerShellQuestion | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> TurnResult:
    """Execute one submitted interactive-shell turn via :meth:`AgentSession.chat`.

    The action driver, gather pass, and conversational assistant default to the
    shell adapters but are overridable via ``execute_actions`` / ``gather_evidence``
    / ``answer_agent`` (the test injection seams, typed in ``turn_seams``). Prefer
    a long-lived ``action_runner`` for the REPL so the core action stack is not
    rebuilt every turn; the one-shot path is only for tests and cold entrypoints.
    """
    resolved_output = resolve_output_sink(console, output)
    runner: ShellActionRunner | None = None
    injected_execute: RunActionToolTurn | None = execute_actions
    if injected_execute is None:
        runner = action_runner or ShellActionRunner(
            session=session,
            console=console,
            request_exit=request_exit,
            output=output,
            tool_hooks=tool_hooks,
        )
        if action_runner is not None:
            runner.bind_turn(console=console, output=output, tool_hooks=tool_hooks)

    bindings = _ShellTurnBindings(
        session=session,
        console=console,
        gather=gather_evidence or gather_integration_tool_evidence,
        answer=answer_agent or answer_shell_question,
        output=resolved_output,
        action_runner=runner,
        execute=injected_execute,
        request_exit=request_exit,
        tool_hooks=tool_hooks,
    )
    chat_bindings = ChatTurnBindings(
        execute_actions=bindings.execute_actions,
        answer=bindings.answer_question,
        gather=bindings.gather_evidence,
        accounting=ShellTurnAccounting(session=session, text=text, recorder=recorder),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        surface="interactive_shell",
        output=resolved_output,
    )
    # Shell already owns env/session boot; do not reload env per turn.
    agent_session = AgentSession(SessionConfig(load_env=False))
    return agent_session.chat(
        text,
        agent=_ShellChatDispatcher(session=session, bindings=chat_bindings),
    )


__all__ = ["execute_shell_turn"]
