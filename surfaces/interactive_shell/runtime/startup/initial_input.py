"""Non-interactive initial-input replay for REPL startup."""

from __future__ import annotations

from rich.console import Console

from platform.analytics.repl_context import bound_repl_turn_context
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.banner import render_banner
from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from surfaces.interactive_shell.utils.telemetry import PromptRecorder

_TURN_KIND = "agent"


def run_initial_input(
    initial_input: str,
    session: Session,
) -> int:
    # Imported lazily so importing this module during REPL boot (main.py imports
    # ``run_initial_input`` at top) does not pull the harness/turn-execution
    # stack into the base import path when there is no initial input to replay.
    from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

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
        recorder = PromptRecorder.start(session=session, text=stripped, turn_kind=_TURN_KIND)
        with bound_repl_turn_context(
            session_id=session.session_id,
            turn_kind=_TURN_KIND,
            prompt_turn_id=recorder.turn_id if recorder is not None else None,
        ):
            execute_shell_turn(
                stripped,
                session,
                console,
                recorder=recorder,
                confirm_fn=None,
                is_tty=False,
            )
    return 0


__all__ = ["run_initial_input"]
