"""Test-injection seams for the interactive-shell turn.

The two protocols are the shell-shaped callables a test may inject to replace
a whole stage; the adapters bind one over the agent's ``ExecuteActions`` /
``StreamAnswerFn`` protocols. In production nothing is injected and the
agent's own stages run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from rich.console import Console

from core.agent_harness import OutputSink, ToolCallingTurnResult
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.runtime import HeadlessAgent, TurnPlan
from core.tool import ToolExecutionHooks
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import LlmRunInfo


class RunActionToolTurn(Protocol):
    """Action-selection seam driven by ``execute_shell_turn``.

    ``llm_factory`` is intentionally not part of the contract: ``execute_shell_turn``
    never injects it, and the default adapter supplies its own.
    """

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        request_exit: Callable[[], None] | None = None,
        turn_plan: TurnPlan | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> ToolCallingTurnResult:
        """Run one action turn and return its facts."""


class AnswerShellQuestion(Protocol):
    """Answer seam: respond via the grounded conversational assistant."""

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        request: AnswerRequest,
        output: OutputSink | None = None,
    ) -> LlmRunInfo | None:
        """Answer the question, returning the LLM run info or None."""


@dataclass(frozen=True)
class _InjectedActionStage:
    """Adapts an injected ``RunActionToolTurn`` seam to the ``ExecuteActions`` protocol."""

    seam: RunActionToolTurn
    session: Session
    console: Console
    output: OutputSink
    request_exit: Callable[[], None] | None
    tool_hooks: ToolExecutionHooks | None

    def execute_actions(
        self,
        text: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        return self.seam(
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


@dataclass(frozen=True)
class _InjectedAnswerStage:
    """Adapts an injected ``AnswerShellQuestion`` seam to the ``StreamAnswerFn`` protocol."""

    seam: AnswerShellQuestion
    session: Session
    console: Console
    output: OutputSink

    def answer(self, text: str, request: AnswerRequest) -> LlmRunInfo | None:
        return self.seam(text, self.session, self.console, output=self.output, request=request)


def bind_injected_stages(
    agent: HeadlessAgent,
    session: Session,
    console: Console,
    output: OutputSink,
    *,
    execute_actions: RunActionToolTurn | None,
    answer_agent: AnswerShellQuestion | None,
    request_exit: Callable[[], None] | None,
    tool_hooks: ToolExecutionHooks | None,
) -> None:
    """Bind an adapter over each injected seam (test-only); an omitted seam is the agent's own stage.

    Stated whole per turn, like :class:`TurnBinding`: a stage injected on one
    turn does not carry into the next, so a long-lived REPL agent never keeps
    a test's fake stage by omission. A caller that wants a stage across turns
    passes the seam on every call.
    """
    agent.bind_stages(
        execute_actions=(
            _InjectedActionStage(
                execute_actions, session, console, output, request_exit, tool_hooks
            ).execute_actions
            if execute_actions is not None
            else None
        ),
        answer=(
            _InjectedAnswerStage(answer_agent, session, console, output).answer
            if answer_agent is not None
            else None
        ),
    )


__all__ = [
    "AnswerShellQuestion",
    "RunActionToolTurn",
    "bind_injected_stages",
]
