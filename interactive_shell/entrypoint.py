"""Public REPL entrypoints."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

from config.repl_config import ReplConfig
from core.domain.alerts import inbox as _alert_inbox
from interactive_shell.controller import InteractiveShellController
from interactive_shell.runtime.startup.first_launch_github import require_startup_github_login
from interactive_shell.runtime.startup.initial_input import run_initial_input
from interactive_shell.session import create_repl_runtime_context
from interactive_shell.ui import DIM, render_banner
from interactive_shell.ui import input_prompt as _input_prompt
from tools.fleet_monitoring.sweep import run_startup_sweep

log = logging.getLogger(__name__)
_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


@contextmanager
def _alert_listener(cfg: ReplConfig) -> Iterator[_alert_inbox.AlertInbox | None]:
    if not cfg.alert_listener_enabled:
        yield None
        return

    inbox: _alert_inbox.AlertInbox | None = None
    handle: _alert_inbox.AlertListenerHandle | None = None
    try:
        inbox = _alert_inbox.AlertInbox()
        handle = _alert_inbox.start_alert_listener(
            inbox,
            host=cfg.alert_listener_host,
            port=cfg.alert_listener_port,
            token=cfg.alert_listener_token,
        )
        _alert_inbox.set_current_inbox(inbox)
        _console.print(f"[{DIM}]listening for alerts on http://{handle.bound_address}/alerts[/]")
    except Exception as exc:
        log.warning("Alert listener could not start: %s — continuing without it.", exc)
    try:
        yield inbox
    finally:
        if handle is not None:
            handle.stop()
            _alert_inbox.set_current_inbox(None)


async def repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    from platform.analytics.cli import identify_saved_github_username

    identify_saved_github_username()

    cfg = _config or ReplConfig.load()
    pt_session = _input_prompt._build_prompt_session()
    runtime_context = create_repl_runtime_context(pt_session=pt_session)
    session = runtime_context.session

    if initial_input:
        session.warm_resolved_integrations()
        return run_initial_input(initial_input, session)

    # Open the session file now that we know this is an interactive REPL run.
    session.storage.open_session(session)

    try:
        with _alert_listener(cfg) as inbox:
            runtime_context.inbox = inbox
            await InteractiveShellController(runtime_context).start_interactive_shell()
        return 0
    finally:
        session.storage.flush(session)


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    cfg = config or ReplConfig.load()
    if not cfg.enabled:
        return 0
    if not sys.stdin.isatty() and initial_input is None:
        return 0

    run_startup_sweep()

    if not initial_input:
        render_banner(_console)
        if not require_startup_github_login(_console):
            return 0

    try:
        return asyncio.run(repl_main(initial_input=initial_input, _config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["repl_main", "run_repl"]
