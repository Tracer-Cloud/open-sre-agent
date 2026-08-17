"""Compose one interactive-shell turn on the harness's ``HeadlessAgent``.

The shell builds its agent on the default port family with its own ports
(prompt provider, console sink, error reporter, gather progress and
persistence, tool-provider port factories) and drives the agent's own stages.
Only a caller that injects a whole stage (``execute_actions`` /
``gather_evidence`` / ``answer_agent`` — the test seams typed in
``turn_seams``) gets an adapter bound over it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from core.agent_harness import (
    OutputSink,
    ToolCallingTurnResult,
    TurnResult,
)
from core.agent_harness.ports import AnswerRequest, GatheredEvidence
from core.agent_harness.runtime import HeadlessAgent, TurnBinding
from core.agent_harness.spi.cancel import host_cancel_requested
from core.agent_harness.spi.session_goal import (
    SessionGoal,
    format_session_goal_progress,
)
from core.agent_harness.turns.turn_plan import TurnPlan
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
from surfaces.interactive_shell.runtime.turn_seams import (
    AnswerShellQuestion,
    GatherEvidence,
    RunActionToolTurn,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder


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


@dataclass(frozen=True)
class _InjectedGatherStage:
    """Adapts an injected ``GatherEvidence`` seam to the ``EvidenceGatherer`` protocol."""

    seam: GatherEvidence
    session: Session
    console: Console

    def gather_evidence(
        self, text: str, *, turn_plan: TurnPlan | None = None
    ) -> str | GatheredEvidence | None:
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return self.seam(text, self.session, self.console, resolved_integrations=resolved)


def _bind_injected_stages(
    agent: HeadlessAgent,
    session: Session,
    console: Console,
    output: OutputSink,
    *,
    execute_actions: RunActionToolTurn | None,
    answer_agent: AnswerShellQuestion | None,
    gather_evidence: GatherEvidence | None,
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
        gather_evidence=(
            _InjectedGatherStage(gather_evidence, session, console).gather_evidence
            if gather_evidence is not None
            else None
        ),
    )


def execute_shell_turn(
    text: str,
    session: Session,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    agent: HeadlessAgent | None = None,
    execute_actions: RunActionToolTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerShellQuestion | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> TurnResult:
    """Execute one submitted interactive-shell turn via :meth:`HeadlessAgent.handle`.

    Pass a long-lived ``agent`` (the REPL builds one at startup and rebinds it
    per turn) so the tool stack is not rebuilt every turn; without one, an agent
    is built for this call. ``execute_actions`` / ``gather_evidence`` /
    ``answer_agent`` replace a whole stage — the test injection seams typed in
    ``turn_seams``.
    """
    resolved_output = resolve_output_sink(console, output)
    if agent is None:
        agent = build_shell_agent(
            session, console, output=resolved_output, request_exit=request_exit
        )
    binding = TurnBinding(
        session=session,
        output=resolved_output,
        tool_hooks=tool_hooks,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )
    _bind_injected_stages(
        agent,
        session,
        console,
        resolved_output,
        execute_actions=execute_actions,
        answer_agent=answer_agent,
        gather_evidence=gather_evidence,
        request_exit=request_exit,
        tool_hooks=tool_hooks,
    )

    def _on_progress(goal: SessionGoal) -> None:
        rendered = format_session_goal_progress(goal, session=session)
        if rendered:
            # Checklist uses ``[x]`` / ``[ ]`` — Rich markup must stay off.
            console.print(rendered, markup=False)

    def _accounting(message: str) -> ShellTurnAccounting:
        return ShellTurnAccounting(session=session, text=message, recorder=recorder)

    return agent.handle(
        text,
        binding,
        accounting_factory=_accounting,
        cancel_requested=lambda: host_cancel_requested(resolved_output),
        on_progress=_on_progress,
    )


__all__ = ["execute_shell_turn"]
