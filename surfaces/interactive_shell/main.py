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
from surfaces.interactive_shell.ui.banner import render_banner
from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from tools.system.fleet_monitoring.sweep import run_startup_sweep

_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


async def run_repl_async(
    initial_input: str | None = None,
    _config: ReplConfig | None = None,
    resume_session_id: str | None = None,
) -> int:
    from platform.analytics.cli import identify_saved_github_username

    identify_saved_github_username()

    cfg = _config or ReplConfig.load()
    pt_session = build_prompt_session()
    runtime_context = create_repl_runtime_context(pt_session=pt_session)
    session = runtime_context.session

    if initial_input:
        session.warm_resolved_integrations()
        return run_initial_input(initial_input, session)

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
                _console,
                slash_command=slash_command,
            ):
                return 1

        await InteractiveShellController(
            runtime_context,
            config=cfg,
            console=_console,
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
) -> int:
    cfg = config or ReplConfig.load()
    if not cfg.enabled and not resume_session_id:
        return 0
    if not sys.stdin.isatty() and initial_input is None:
        return 0

    run_startup_sweep()

    try:
        if not initial_input:
            render_banner(_console)
            if not require_startup_github_login(_console):
                return 0

        return asyncio.run(
            run_repl_async(
                initial_input=initial_input,
                _config=cfg,
                resume_session_id=resume_session_id,
            )
        )
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl", "run_repl_async"]
