"""Public REPL entrypoints."""

from __future__ import annotations

import asyncio
import logging
import sys

from rich.console import Console

from app.agents.sweep import run_startup_sweep
from app.cli.interactive_shell import alert_inbox as _alert_inbox
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.runtime.dispatch import run_initial_input
from app.cli.interactive_shell.runtime.loop import run_interactive
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskRegistry
from app.cli.interactive_shell.ui import DIM, render_banner

log = logging.getLogger(__name__)


async def repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    cfg = _config or ReplConfig.load()
    session = ReplSession()
    session.task_registry = TaskRegistry.persistent()
    pt_session = _prompt_surface._build_prompt_session()
    session.prompt_history_backend = pt_session.history

    if initial_input:
        return run_initial_input(initial_input, session)

    alert_listener_handle: _alert_inbox.AlertListenerHandle | None = None
    inbox: _alert_inbox.AlertInbox | None = None
    if cfg.alert_listener_enabled:
        try:
            inbox = _alert_inbox.AlertInbox()
            alert_listener_handle = _alert_inbox.start_alert_listener(
                inbox,
                host=cfg.alert_listener_host,
                port=cfg.alert_listener_port,
                token=cfg.alert_listener_token,
            )
            _alert_inbox.set_current_inbox(inbox)
            console = Console(
                highlight=False,
                force_terminal=True,
                color_system="truecolor",
                legacy_windows=False,
            )
            console.print(
                f"[{DIM}]listening for alerts on http://{alert_listener_handle.bound_address}/alerts[/]"
            )
        except Exception as exc:
            log.warning("Alert listener could not start: %s — continuing without it.", exc)

    try:
        await run_interactive(session, pt_session=pt_session, inbox=inbox)
        return 0
    finally:
        if alert_listener_handle is not None:
            alert_listener_handle.stop()
            _alert_inbox.set_current_inbox(None)


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    cfg = config or ReplConfig.load()
    if not cfg.enabled:
        return 0
    if not sys.stdin.isatty() and initial_input is None:
        return 0

    run_startup_sweep()

    if not initial_input:
        real_console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        render_banner(real_console)

    try:
        return asyncio.run(repl_main(initial_input=initial_input, _config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["repl_main", "run_repl"]
