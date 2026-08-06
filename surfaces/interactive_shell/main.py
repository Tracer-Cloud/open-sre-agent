"""Public REPL entrypoints."""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console

from config.repl_config import ReplConfig
from core.agent_harness.session import SessionManager
from surfaces.interactive_shell.controller import InteractiveShellController
from surfaces.interactive_shell.runtime.context import create_repl_runtime_context
from surfaces.interactive_shell.runtime.startup.first_launch_github import (
    require_startup_github_login,
)
from surfaces.interactive_shell.runtime.startup.initial_input import run_initial_input
from surfaces.interactive_shell.runtime.startup.loop_suggestions import offer_loop_suggestions
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.terminal_ui import render_terminal_ui
from tools.system.fleet_monitoring.sweep import run_startup_sweep

# Fallback when a caller does not supply one. Forces a terminal because the
# shell owns the screen; an embedding caller passes its own instead.
_DEFAULT_CONSOLE = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


async def run_repl_async(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    resume_session_id: str | None = None,
    console: Console | None = None,
) -> int:
    """Run the shell on an existing event loop and return its exit code."""
    from platform.analytics.cli import identify_saved_github_username

    identify_saved_github_username()

    cfg = config or ReplConfig.load()
    out = console or _DEFAULT_CONSOLE
    pt_session = build_prompt_session()
    runtime_context = create_repl_runtime_context(pt_session=pt_session)
    session = runtime_context.session

    if initial_input:
        session.warm_resolved_integrations()
        return run_initial_input(initial_input, session, out)

    # Open the session file now that we know this is an interactive REPL run.
    SessionManager.for_session(session).open_storage(session)

    try:
        if resume_session_id:
            from surfaces.interactive_shell.command_registry.session_cmds.resume import (
                resume_session_by_prefix,
            )

            slash_command = f"/resume {resume_session_id.strip()}"
            if not resume_session_by_prefix(
                resume_session_id.strip(),
                session,
                out,
                slash_command=slash_command,
            ):
                return 1
        else:
            # Fresh interactive start with no scheduled loops: offer the
            # suggested-loops picker before the prompt loop takes stdin.
            offer_loop_suggestions(session)

        await InteractiveShellController(
            runtime_context,
            config=cfg,
            console=out,
        ).start_interactive_shell()
        return 0
    finally:
        # True end-of-run teardown: persist and release the session's resources.
        SessionManager.for_session(session).close(session)


def run_repl(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    *,
    resume_session_id: str | None = None,
    console: Console | None = None,
) -> int:
    """Run the shell on a new event loop and return its exit code."""
    cfg = config or ReplConfig.load()
    out = console or _DEFAULT_CONSOLE
    if not cfg.enabled and not resume_session_id:
        return 0
    if not sys.stdin.isatty() and initial_input is None:
        return 0

    run_startup_sweep()

    try:
        if not initial_input:
            render_terminal_ui(out)
            if not require_startup_github_login(out):
                return 0

        return asyncio.run(
            run_repl_async(
                initial_input=initial_input,
                config=cfg,
                resume_session_id=resume_session_id,
                console=out,
            )
        )
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl", "run_repl_async"]
