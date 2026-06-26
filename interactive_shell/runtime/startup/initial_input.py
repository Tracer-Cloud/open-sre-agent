"""Non-interactive initial-input replay for REPL startup."""

from __future__ import annotations

from rich.console import Console

from interactive_shell.runtime.controller import execute_routed_turn
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.ui import render_banner
from interactive_shell.ui.input_prompt.rendering import render_submitted_prompt


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
        execute_routed_turn(stripped, session, console, is_tty=False)
    return 0


__all__ = ["run_initial_input"]
