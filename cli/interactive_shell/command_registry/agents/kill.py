"""/fleet kill subcommand."""

from __future__ import annotations

import os
from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from platform.analytics.events import Event
from platform.analytics.provider import get_analytics
from tools.fleet_monitoring.lifecycle import TerminateResult, terminate
from tools.fleet_monitoring.registry import AgentRegistry

# Type alias for the optional confirmation callback (used for testing).
_ConfirmFn = Callable[[str], str]


def _cmd_agents_kill(
    session: ReplSession,
    console: Console,
    args: list[str],
    *,
    confirm_fn: _ConfirmFn | None = None,
) -> bool:
    """Handle ``/fleet kill <pid> [--force]``.

    Sends SIGTERM, waits up to 5 s, then escalates to SIGKILL.
    Asks for confirmation unless ``--force`` is present.
    Emits an ``agent_killed`` analytics event on success.
    """
    force = "--force" in args
    positional = [a for a in args if a != "--force"]

    if not positional:
        console.print(f"[{ERROR}]usage:[/] /fleet kill <pid> [--force]")
        session.mark_latest(ok=False, kind="slash")
        return True

    raw_pid = positional[0]
    try:
        pid = int(raw_pid)
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(raw_pid)} is not an integer")
        session.mark_latest(ok=False, kind="slash")
        return True

    if pid == os.getpid():
        console.print(f"[{ERROR}]refusing to kill the opensre process itself[/]")
        session.mark_latest(ok=False, kind="slash")
        return True

    # Look up agent name from registry for friendlier output.
    registry = AgentRegistry()
    record = registry.get(pid)
    label = f"{record.name} (pid {pid})" if record else f"pid {pid}"

    if not force:
        prompt_text = f"About to SIGTERM {label}. Confirm? [y/N] "
        if confirm_fn is not None:
            answer = confirm_fn(prompt_text)
        else:
            answer = console.input(prompt_text)
        if answer.strip().lower() not in ("y", "yes"):
            console.print(f"[{DIM}]aborted.[/]")
            return True

    try:
        result: TerminateResult = terminate(pid)
    except ProcessLookupError:
        console.print(f"[{ERROR}]no such process:[/] pid {pid}")
        session.mark_latest(ok=False, kind="slash")
        return True
    except PermissionError:
        console.print(f"[{ERROR}]permission denied:[/] cannot signal pid {pid}")
        session.mark_latest(ok=False, kind="slash")
        return True

    if result.exited:
        console.print(
            f"[{HIGHLIGHT}]Sent {result.signal_sent}. "
            f"Process exited after {result.elapsed_seconds:.1f}s.[/]"
        )
    else:
        console.print(
            f"[{WARNING}]Sent {result.signal_sent} but process may still be running "
            f"after {result.elapsed_seconds:.1f}s.[/]"
        )
        session.mark_latest(ok=False, kind="slash")

    # Remove from the agent registry so `/fleet` no longer shows the dead PID.
    # Only forget when the process actually exited — otherwise it stays visible
    # for further monitoring or another kill attempt.
    if record is not None and result.exited:
        registry.forget(pid)

    event = Event.AGENT_KILLED if result.exited else Event.AGENT_KILL_FAILED
    get_analytics().capture(
        event,
        {
            "pid": str(pid),
            "agent_name": record.name if record else "unknown",
            "signal": result.signal_sent,
            "exited": result.exited,
            "elapsed_seconds": str(round(result.elapsed_seconds, 2)),
        },
    )
    return True
