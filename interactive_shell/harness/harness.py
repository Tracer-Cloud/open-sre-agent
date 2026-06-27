"""Public entrypoint for interactive-shell agent turns."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from interactive_shell.chat.cli_agent import answer_cli_agent
from interactive_shell.chat.tool_gathering import gather_tool_evidence
from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
    execute_cli_actions,
)
from interactive_shell.harness.turn_loop import (
    AnswerAgent,
    ExecuteActions,
    GatherEvidence,
    ShellTurnContext,
    ShellTurnDeps,
    ShellTurnResult,
    TurnHooks,
    run_shell_turn,
)
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from platform.analytics.cli import capture_terminal_turn_summarized


class ShellHarness:
    """Orchestrates interactive-shell turns: owns session wiring, deps, and hooks."""

    def __init__(
        self,
        session: ReplSession,
        *,
        execute_actions: ExecuteActions | None = None,
        gather_evidence: GatherEvidence | None = None,
        answer_agent: AnswerAgent | None = None,
        hooks: TurnHooks | None = None,
    ) -> None:
        self._session = session
        self._deps = ShellTurnDeps(
            execute_actions=execute_actions or execute_cli_actions,
            gather_evidence=gather_evidence or gather_tool_evidence,
            answer_agent=answer_agent or answer_cli_agent,
            capture_terminal_turn=capture_terminal_turn_summarized,
            hooks=hooks,
        )

    def handle_turn(
        self,
        text: str,
        console: Console,
        *,
        recorder: PromptRecorder | None = None,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
    ) -> ShellTurnResult:
        return run_shell_turn(
            ShellTurnContext(
                text=text,
                session=self._session,
                console=console,
                recorder=recorder,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            ),
            self._deps,
        )


def handle_message_with_agent(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    execute_actions: Callable[..., TerminalActionExecutionResult] | None = None,
    answer_agent: Callable[..., LlmRunInfo | None] | None = None,
) -> None:
    """Handle one interactive-shell turn end to end."""
    ShellHarness(
        session,
        execute_actions=execute_actions,
        answer_agent=answer_agent,
    ).handle_turn(
        text,
        console,
        recorder=recorder,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


__all__ = ["ShellHarness", "handle_message_with_agent"]
