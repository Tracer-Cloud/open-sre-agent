"""Non-interactive initial-input replay for REPL startup."""

from __future__ import annotations

from rich.console import Console

from context.session import ReplSession
from interactive_shell.agent_shell.agent import handle_message_with_agent
from interactive_shell.ui import render_banner
from interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from interactive_shell.utils.telemetry import PromptRecorder
from platform.analytics.repl_context import bind_cli_session_id, reset_cli_session_id

_TURN_KIND = "agent"


def run_initial_input(
    initial_input: str,
    session: ReplSession,
) -> int:
    console = Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_banner(console)
    for line in initial_input.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        session_token = bind_cli_session_id(session.session_id)
        try:
            recorder = PromptRecorder.start(session=session, text=stripped, turn_kind=_TURN_KIND)
            handle_message_with_agent(
                stripped,
                session,
                console,
                recorder=recorder,
                confirm_fn=None,
                is_tty=False,
            )
        finally:
            reset_cli_session_id(session_token)
    return 0


__all__ = ["run_initial_input"]
