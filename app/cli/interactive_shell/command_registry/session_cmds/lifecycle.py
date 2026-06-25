"""Session lifecycle slash commands: /clear and /new."""

from __future__ import annotations

from rich.console import Console

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, HIGHLIGHT


def _cmd_clear(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.interactive_shell.ui import render_ready_box

    console.clear()
    render_ready_box(console, session=session)
    return True


def _cmd_new(session: ReplSession, console: Console, _args: list[str]) -> bool:
    """Start a new session while preserving the current LLM conversation context.

    Unlike /clear (which only clears the screen), /new rotates the session ID
    and resets all session state while keeping cli_agent_messages and
    accumulated_context so a resumed or in-progress conversation continues
    seamlessly in a fresh session file.
    """
    from app.cli.interactive_shell.sessions.store import SessionStore

    saved_messages = list(session.cli_agent_messages)
    saved_context = dict(session.accumulated_context)
    saved_resumed_name = session.resumed_from_name

    SessionStore.flush(session)
    session.clear()

    session.cli_agent_messages = saved_messages
    session.accumulated_context = saved_context
    session.resumed_from_name = saved_resumed_name

    SessionStore.open_session(session)
    console.print(
        f"[{DIM}]new session started[/] [{HIGHLIGHT}]—[/] [{DIM}]conversation context carried forward.[/]"
    )
    if saved_messages:
        console.print(f"[{DIM}]  {len(saved_messages)} messages in context · type to continue[/]")
    return True
