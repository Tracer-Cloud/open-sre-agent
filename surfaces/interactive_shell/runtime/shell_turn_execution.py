"""One interactive-shell turn: build (or reuse) the shell agent, then ``handle``.

The shell's ports are supplied by ``shell_agent``; the agent's own stages run.
A test that injects a whole stage (``execute_actions`` / ``gather_evidence`` /
``answer_agent``) goes through the seams in ``turn_seams``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness import (
    OutputSink,
    TurnResult,
)
from core.agent_harness.runtime import HeadlessAgent, TurnBinding
from core.agent_harness.spi.cancel import host_cancel_requested
from core.agent_harness.spi.session_goal import (
    SessionGoal,
    format_session_goal_progress,
)
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
from surfaces.interactive_shell.runtime.turn_seams import (
    AnswerShellQuestion,
    GatherEvidence,
    RunActionToolTurn,
    bind_injected_stages,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.utils.telemetry import PromptRecorder


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
    bind_injected_stages(
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
