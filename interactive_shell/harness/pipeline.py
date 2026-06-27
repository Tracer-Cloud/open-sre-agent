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
from interactive_shell.harness.turn_loop import ShellTurnContext, ShellTurnDeps, run_shell_turn
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from platform.analytics.cli import capture_terminal_turn_summarized


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
    run_shell_turn(
        ShellTurnContext(
            text=text,
            session=session,
            console=console,
            recorder=recorder,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
        ),
        ShellTurnDeps(
            execute_actions=execute_cli_actions if execute_actions is None else execute_actions,
            gather_evidence=gather_tool_evidence,
            answer_agent=answer_cli_agent if answer_agent is None else answer_agent,
            capture_terminal_turn=capture_terminal_turn_summarized,
        ),
    )


__all__ = ["handle_message_with_agent"]
