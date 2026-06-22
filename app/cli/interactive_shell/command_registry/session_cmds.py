"""Slash commands: session lifecycle (/clear, /new, /sessions, /resume)."""

from __future__ import annotations

import contextlib
from collections import deque

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    repl_table,
)
from app.cli.interactive_shell.ui.choice_menu import repl_choose_one, repl_tty_interactive
from app.cli.interactive_shell.ui.time_format import format_repl_duration, format_repl_timestamp


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


def _record_resume_slash(
    session: ReplSession,
    args: list[str],
    *,
    ok: bool = True,
    picked_id: str | None = None,
) -> None:
    """Record /resume in the active session file after identity is settled."""
    if picked_id:
        text = f"/resume {picked_id[:8]}"
    elif args:
        text = f"/resume {' '.join(args)}"
    else:
        text = "/resume"
    session.record("slash", text, ok=ok)


def _cmd_sessions(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from datetime import UTC, datetime

    from app.cli.interactive_shell.sessions.store import SessionStore

    entries = SessionStore.load_recent(20)
    if not entries:
        console.print(f"[{DIM}]No sessions recorded yet.[/]")
        return True

    table = repl_table(title="Recent sessions\n", title_style=BOLD_BRAND)
    table.add_column("#", style="bold", justify="right")
    table.add_column("Session ID", style="bold")
    table.add_column("Name")
    table.add_column("Started")
    table.add_column("Duration")
    table.add_column("Turns", justify="right")
    table.add_column("Investigations", justify="right")

    for i, entry in enumerate(entries, start=1):
        sid = entry["session_id"]
        short_id = sid[:8] if len(sid) >= 8 else sid
        is_current = sid == session.session_id

        name = entry.get("name") or ""
        if is_current and not name and session.resumed_from_name:
            name = f"↩ {session.resumed_from_name}"
        if is_current:
            name_col = f"[{DIM}](current)[/]" if not name else f"{escape(name)} [{DIM}](current)[/]"
        else:
            name_col = escape(name) if name else f"[{DIM}]—[/]"

        started_str = format_repl_timestamp(entry.get("started_at"), style="table")

        duration_secs = entry.get("duration_secs")
        if is_current:
            with contextlib.suppress(OSError, OverflowError, ValueError):
                elapsed = int(
                    (
                        datetime.now(UTC) - datetime.fromtimestamp(session.started_at, tz=UTC)
                    ).total_seconds()
                )
                duration_secs = elapsed

        total = entry.get("total_turns")
        investigations = entry.get("investigation_turns")

        table.add_row(
            str(i),
            short_id,
            name_col,
            started_str,
            format_repl_duration(duration_secs),
            str(total) if total is not None else "—",
            str(investigations) if investigations is not None else "—",
        )

    print_repl_table(console, table)
    return True


def _interactive_resume_menu(session: ReplSession, console: Console) -> bool:
    """Show a numbered list of recent sessions and resume the selected one."""
    from app.cli.interactive_shell.sessions.store import SessionStore

    entries = [e for e in SessionStore.load_recent(10) if e["session_id"] != session.session_id]
    if not entries:
        console.print(f"[{DIM}]No previous sessions to resume.[/]")
        return True

    choices: list[tuple[str, str]] = []
    for entry in entries:
        sid = entry["session_id"]
        short_id = sid[:8]
        name = entry.get("name") or f"[{short_id}]"
        started_str = format_repl_timestamp(entry.get("started_at"), style="compact")
        label = f"{name[:40]:<40}  {short_id}  {started_str}"
        choices.append((sid, label))
    choices.append(("done", "done"))

    picked = repl_choose_one(title="resume session", breadcrumb="/resume", choices=choices)
    if picked is None or picked == "done":
        return True

    slash_command = f"/resume {picked[:8]}"
    if not _do_resume(picked, session, console, slash_command=slash_command):
        _record_resume_slash(session, [], picked_id=picked, ok=False)
    return True


_HISTORY_DISPLAY_CHAT_KINDS: frozenset[str] = frozenset(
    {"chat", "cli_agent", "cli_help", "follow_up", "alert", "incoming_alert"}
)


def _response_for_prompt(turn_details: list[dict], prompt: str) -> str:
    for detail in turn_details:
        if detail.get("prompt") == prompt:
            return str(detail.get("response") or "")
    return ""


def _render_resumed_session_history(
    console: Console,
    *,
    history: list[dict],
    turn_details: list[dict],
    messages: list[tuple[str, str]],
) -> None:
    """Render prior session activity in REPL turn order, including slash commands."""
    from rich.markdown import Markdown

    from app.cli.interactive_shell.ui.streaming import render_response_header
    from app.cli.interactive_shell.ui.theme import MARKDOWN_THEME

    if not history and not messages:
        return

    console.print(f"[{DIM}]─── conversation history ─────────────────────────────────[/]")

    if history:
        assistant_by_user: dict[str, deque[str]] = {}
        pending_user: str | None = None
        for role, text in messages:
            if role == "user":
                pending_user = text
            elif role == "assistant" and pending_user is not None:
                assistant_by_user.setdefault(pending_user, deque()).append(text)
                pending_user = None

        for rec in history:
            kind = rec.get("kind", "")
            text = rec.get("text") or ""
            if kind == "slash":
                console.print(f"[bold]$ {escape(text)}[/bold]")
                continue
            if kind not in _HISTORY_DISPLAY_CHAT_KINDS or not text:
                continue
            console.print(f"[bold {HIGHLIGHT}]❯[/] {escape(text)}")
            response = _response_for_prompt(turn_details, text)
            if not response:
                queued = assistant_by_user.get(text)
                response = queued.popleft() if queued else ""
            if response:
                render_response_header(console, "assistant")
                with console.use_theme(MARKDOWN_THEME):
                    console.print(Markdown(response, code_theme="ansi_dark"))
        console.print(f"[{DIM}]─────────────────────────────────────────────────────────[/]")
        return

    has_pending_user = False
    for role, text in messages:
        if role == "user":
            console.print(f"[bold {HIGHLIGHT}]❯[/] {escape(text)}")
            has_pending_user = True
        elif role == "assistant" and has_pending_user:
            render_response_header(console, "assistant")
            with console.use_theme(MARKDOWN_THEME):
                console.print(Markdown(text, code_theme="ansi_dark"))
            has_pending_user = False
    console.print(f"[{DIM}]─────────────────────────────────────────────────────────[/]")


def _apply_resume_data(
    data: dict,
    session: ReplSession,
    console: Console,
    *,
    slash_command: str | None = None,
) -> bool:
    """Apply loaded session data into the running session and print a summary."""
    messages = data.get("cli_agent_messages") or []
    context = data.get("accumulated_context") or {}
    history = data.get("history") or []
    has_snapshot = data.get("has_snapshot", False)
    sid = data.get("session_id", "")
    short_id = sid[:8] if len(sid) >= 8 else sid
    name = data.get("name") or ""

    if not messages and not context:
        console.print(
            f"[{DIM}]session {short_id} has no conversation to resume "
            "(no chat turns or context found).[/]"
        )
        if not data.get("turn_details") and not has_snapshot:
            console.print(
                f"[{DIM}]tip: turn_detail records are only written when prompt logging is enabled.[/]"
            )
        if slash_command:
            session.record("slash", slash_command, ok=False)
        return True

    existing = session.cli_agent_messages
    if existing:
        console.print(
            f"[{WARNING}]current session has {len(existing)} messages — "
            "they will be replaced by the resumed context.[/]"
        )

    from datetime import datetime

    from app.cli.interactive_shell.sessions.store import SessionStore

    target_sid = sid
    if session.session_id != target_sid:
        SessionStore.flush(session)
        session.clear(rotate_identity=False)
        session.session_id = target_sid
        started_raw = data.get("started_at")
        if started_raw:
            with contextlib.suppress(Exception):
                session.started_at = datetime.fromisoformat(started_raw).timestamp()
        SessionStore.reopen_session(target_sid)
    else:
        session.clear(rotate_identity=False)
        session.session_id = target_sid

    session.cli_agent_messages = list(messages)
    session.accumulated_context = dict(context)

    if history:
        session.history = list(history) + session.history

    source = "snapshot" if has_snapshot else "turn records"
    name_str = f" · {escape(name)}" if name else ""
    console.print(
        f"[{HIGHLIGHT}]resumed session {short_id}{name_str}[/] "
        f"[{DIM}]({len(messages)} messages in context from {source})[/]"
    )

    _render_resumed_session_history(
        console,
        history=history,
        turn_details=data.get("turn_details") or [],
        messages=list(messages),
    )

    if context:
        console.print(
            f"[{DIM}]accumulated context restored:[/] "
            + ", ".join(f"{escape(k)}={escape(str(v))}" for k, v in sorted(context.items()))
        )

    if slash_command:
        session.record("slash", slash_command)

    return True


def _do_resume(
    prefix: str,
    session: ReplSession,
    console: Console,
    *,
    slash_command: str | None = None,
) -> bool:
    """Load session by ID prefix and restore context into the running session."""
    from app.cli.interactive_shell.sessions.store import SessionStore

    data = SessionStore.load_session(prefix)
    if data is None:
        n = SessionStore.count_prefix_matches(prefix)
        if n > 1:
            console.print(
                f"[{WARNING}]ambiguous prefix '{escape(prefix)}' matches {n} sessions — "
                "use more characters.[/]"
            )
        else:
            console.print(f"[{ERROR}]session '{escape(prefix)}' not found.[/]")
        return False
    return _apply_resume_data(data, session, console, slash_command=slash_command)


def _cmd_resume(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_resume_menu(session, console)

    if not args:
        console.print(f"[{DIM}]usage: /resume <session-id-prefix>[/]")
        console.print(f"[{DIM}]run /sessions to list session IDs.[/]")
        _record_resume_slash(session, args)
        return True

    prefix = args[0].strip()

    if session.session_id.startswith(prefix):
        console.print(
            f"[{DIM}]session {prefix[:8]} is the current session — "
            "run /sessions to pick a previous one.[/]"
        )
        _record_resume_slash(session, args)
        return True

    from app.cli.interactive_shell.sessions.store import SessionStore

    data = SessionStore.load_session(prefix)
    if data is None and len(prefix) >= 3:
        candidates = [
            e
            for e in SessionStore.load_recent(20)
            if prefix.lower() in (e.get("name") or "").lower()
            and e["session_id"] != session.session_id
        ]
        if len(candidates) == 1:
            data = SessionStore.load_session(candidates[0]["session_id"])
        elif len(candidates) > 1:
            console.print(
                f"[{WARNING}]'{escape(prefix)}' matches {len(candidates)} sessions by name — "
                "use a session ID prefix or be more specific.[/]"
            )
            _record_resume_slash(session, args, ok=False)
            return True

    if data is None:
        n = SessionStore.count_prefix_matches(prefix)
        if n > 1:
            console.print(
                f"[{WARNING}]ambiguous prefix '{escape(prefix)}' matches {n} sessions — "
                "use more characters.[/]"
            )
        else:
            console.print(f"[{ERROR}]session '{escape(prefix)}' not found.[/]")
        _record_resume_slash(session, args, ok=False)
        return True

    slash_command = f"/resume {' '.join(args)}" if args else "/resume"
    _apply_resume_data(data, session, console, slash_command=slash_command)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/clear", "Clear the screen and re-render the banner.", _cmd_clear),
    SlashCommand(
        "/sessions",
        "List recent REPL sessions.",
        _cmd_sessions,
        usage=("/sessions",),
    ),
    SlashCommand(
        "/resume",
        "Resume a previous session by restoring its conversation context.",
        _cmd_resume,
        usage=("/resume <session-id-prefix>",),
        notes=(
            "Restores cli_agent_messages and accumulated infra context from the chosen session.",
            "Bare /resume opens an interactive session picker in a TTY.",
            "Accepts a session ID prefix or a name substring (e.g. /resume redis).",
            "Replaces the current session's LLM conversation context; warns if messages exist.",
        ),
    ),
    SlashCommand(
        "/new",
        "Start a new session while keeping the current conversation context.",
        _cmd_new,
        notes=(
            "Unlike /clear, /new rotates the session ID and resets state while keeping LLM context.",
            "Use after /resume to continue a conversation in a clean session file.",
        ),
    ),
]

__all__ = ["COMMANDS", "_apply_resume_data"]
