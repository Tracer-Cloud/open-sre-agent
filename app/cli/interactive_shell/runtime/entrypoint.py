"""Public REPL entrypoints."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys

from rich.console import Console

from app.cli.interactive_shell import alert_inbox as _alert_inbox
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.runtime.dispatch import run_initial_input
from app.cli.interactive_shell.runtime.loop import run_interactive
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskRegistry
from app.cli.interactive_shell.sessions.store import SessionStore
from app.cli.interactive_shell.ui import DIM, render_banner
from app.fleet_monitoring.sweep import run_startup_sweep

log = logging.getLogger(__name__)


def _hydrate_configured_integrations(session: ReplSession) -> None:
    """Record configured integrations (env + local store) on the session.

    Without this the agent can't answer "is X installed?" and the integration
    guards stay dead (``configured_integrations_known`` never flips). Delegates
    to :meth:`ReplSession.hydrate_configured_integrations` so boot-time
    hydration and post-mutation refresh resolve the same env + store set.
    Best-effort: any failure leaves the session in its default "unknown" state.
    """
    session.hydrate_configured_integrations()


async def repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    cfg = _config or ReplConfig.load()
    session = ReplSession()
    _hydrate_configured_integrations(session)
    session.task_registry = TaskRegistry.persistent()
    pt_session = _prompt_surface._build_prompt_session()
    session.prompt_history_backend = pt_session.history

    if initial_input:
        session.warm_resolved_integrations()
        return run_initial_input(initial_input, session)

    # Open the session file now that we know this is an interactive REPL run.
    SessionStore.open_session(session)

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
        SessionStore.flush(session)


def _github_login_explicitly_bypassed() -> bool:
    """Cheap, dependency-free check for contexts where the GitHub gate is intentionally skipped.

    Used only as the *error* fallback for the first-launch gate. It must not import
    the gate module (that import may be exactly what failed), so it re-derives the
    documented bypasses directly:

    * ``OPENSRE_SKIP_GITHUB_LOGIN`` — the user-facing escape hatch.
    * Non-eligible operating systems — the gate only runs on macOS/Windows.
    * Non-interactive stdin — scripted / CI runs have no prompt to drive.
    """
    if os.getenv("OPENSRE_SKIP_GITHUB_LOGIN", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if platform.system() not in {"Darwin", "Windows"}:
        return True
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _maybe_require_github_login(console: Console) -> bool:
    """Enforce the first-launch GitHub login gate.

    Returns True when the REPL should start (gate not required, or login
    succeeded) and False when startup must not proceed (user quit at the gate, or
    the gate could not run in a context where GitHub sign-in is mandatory).

    On an unexpected error we deliberately do NOT fail open into the REPL: that
    would let a gate bug silently skip the mandatory sign-in. Instead we only
    allow startup when an explicit, documented bypass applies (skip env var,
    ineligible OS, or non-TTY); otherwise we block and point the user at the
    escape hatch so a real outage can never permanently lock them out.
    """
    try:
        from app.cli.first_launch_github import (
            require_github_login_on_first_launch,
            should_require_github_login,
        )

        if not should_require_github_login():
            return True
        return require_github_login_on_first_launch(console)
    except Exception:
        log.warning("First-launch GitHub login gate failed.", exc_info=True)
        if _github_login_explicitly_bypassed():
            return True
        console.print(
            "GitHub sign-in is required to use OpenSRE, but the sign-in step could not run. "
            "Set [bold]OPENSRE_SKIP_GITHUB_LOGIN=1[/bold] to bypass this, then relaunch "
            "[bold]opensre[/bold]."
        )
        return False


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
        if not _maybe_require_github_login(real_console):
            return 0

    try:
        return asyncio.run(repl_main(initial_input=initial_input, _config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["repl_main", "run_repl"]
