"""Compose one interactive-shell turn on the harness's ``HeadlessAgent``.

The shell builds its agent through the same factory the gateway uses,
supplying its own ports (prompt provider, output sink, gather rendering and
persistence, tool-provider port factories) and, when a caller injects one, a
whole stage (``execute_actions`` / ``gather_evidence`` / ``answer_agent``).
The injection contracts live in ``turn_seams``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from core.agent_harness import AgentSession, OutputSink, SessionConfig
from core.agent_harness.ports import AnswerRequest, ExecuteActions
from core.agent_harness.runtime import HeadlessAgent
from core.agent_harness.spi import (
    SessionGoal,
    ToolCallingTurnResult,
    TurnResult,
    format_session_goal_progress,
    host_cancel_requested,
    run_until_session_goal,
)
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.turn_plan import TurnPlan
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.answer_turn import answer_shell_question
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
from surfaces.interactive_shell.runtime.turn_seams import (
    AnswerShellQuestion,
    GatherEvidence,
    RunActionToolTurn,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder


@dataclass(frozen=True)
class _ShellStages:
    """Adapts the shell's answer and gather seams to the harness stage protocols.

    Bound methods are handed to :meth:`HeadlessAgent.bind_stages`; each closes
    over the turn's session, console and output so the seam callables keep
    their shell-shaped signatures (``turn_seams``).
    """

    session: Session
    console: Console
    output: OutputSink
    answer_seam: AnswerShellQuestion
    gather_seam: GatherEvidence

    def answer(self, text: str, request: AnswerRequest) -> LlmRunInfo | None:
        return self.answer_seam(
            text, self.session, self.console, output=self.output, request=request
        )

    def gather_evidence(
        self, text: str, *, turn_plan: TurnPlan | None = None
    ) -> str | GatheredEvidence | None:
        resolved = turn_plan.resolved_integrations if turn_plan is not None else None
        return self.gather_seam(text, self.session, self.console, resolved_integrations=resolved)


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
    """Execute one submitted interactive-shell turn via :meth:`AgentSession.chat`.

    Pass a long-lived ``agent`` (the REPL builds one at startup and rebinds it
    per turn) so the tool stack is not rebuilt every turn; without one, an agent
    is built for this call. ``execute_actions`` / ``gather_evidence`` /
    ``answer_agent`` replace a whole stage — the test injection seams typed in
    ``turn_seams``.
    """
    resolved_output = resolve_output_sink(console, output)
    stages = _ShellStages(
        session=session,
        console=console,
        output=resolved_output,
        answer_seam=answer_agent or answer_shell_question,
        gather_seam=gather_evidence or gather_integration_tool_evidence,
    )
    execute_stage: ExecuteActions | None = None
    if execute_actions is not None:
        execute_stage = _InjectedActionStage(
            execute_actions, session, console, resolved_output, request_exit, tool_hooks
        ).execute_actions
    if agent is None:
        agent = build_shell_agent(
            session, console, output=resolved_output, request_exit=request_exit
        )
    agent.bind_turn(
        session=session,
        output=resolved_output,
        tool_hooks=tool_hooks,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )
    agent.bind_stages(
        execute_actions=execute_stage,
        answer=stages.answer,
        gather_evidence=stages.gather_evidence,
    )
    # Shell already owns env/session boot; do not reload env per turn.
    chat_host = AgentSession(SessionConfig(load_env=False))

    def _chat(message: str) -> TurnResult:
        # Fresh accounting per outer iteration (nudge text changes each turn).
        agent.bind_turn(
            accounting=ShellTurnAccounting(session=session, text=message, recorder=recorder)
        )
        return chat_host.chat(message, agent=agent)

    def _on_progress(goal: SessionGoal) -> None:
        rendered = format_session_goal_progress(goal, session=session)
        if rendered:
            # Checklist uses ``[x]`` / ``[ ]`` — Rich markup must stay off.
            console.print(rendered, markup=False)

    # Always one action-agent turn first. Session-goal loop continues only when that
    # turn (or a prior turn) attached a SessionGoal via structured handoff /
    # explicit host attach — never via user-text keyword detection.
    return run_until_session_goal(
        _chat,
        session,
        text,
        cancel_requested=lambda: host_cancel_requested(resolved_output),
        on_progress=_on_progress,
    ).last_result


__all__ = ["execute_shell_turn"]
