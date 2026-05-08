"""Slash commands: history management and privacy controls (/history, /privacy)."""

from __future__ import annotations

from prompt_toolkit.history import FileHistory, InMemoryHistory
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.history import (
    clear_persisted_history,
    load_command_history_entries,
    prompt_history_path,
)
from app.cli.interactive_shell.history_policy import (
    DEFAULT_REDACTION_RULES,
    RedactingFileHistory,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


def _show_history(console: Console) -> bool:
    entries = load_command_history_entries()
    if not entries:
        console.print("[dim]no history yet.[/dim]")
        return True

    table = Table(title="Command history", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("#", style="dim", justify="right")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), escape(entry))
    console.print(table)
    return True


def _history_clear(session: ReplSession, console: Console) -> bool:  # noqa: ARG001
    if clear_persisted_history():
        console.print(
            "[green]cleared[/green] persistent history. Up-arrow recall resets on next launch."
        )
    else:
        console.print(
            f"[red]could not clear history[/red] (file system error). path: {prompt_history_path()}"
        )
    return True


def _history_pause(session: ReplSession, console: Console, *, paused: bool) -> bool:
    backend = session.prompt_history_backend
    if isinstance(backend, RedactingFileHistory):
        backend.paused = paused
        state = "off" if paused else "on"
        console.print(f"[dim]history persistence is now {state} for this session.[/dim]")
        return True
    if isinstance(backend, FileHistory):
        if paused:
            console.print(
                "[dim]history is persisting to disk without redaction. "
                "Restart with OPENSRE_HISTORY_REDACT=1 to enable runtime pause support, "
                "or OPENSRE_HISTORY_ENABLED=0 to disable persistence entirely.[/dim]"
            )
            return True
        console.print("[dim]history persistence is already on (raw file history).[/dim]")
        return True
    if backend is None or isinstance(backend, InMemoryHistory):
        if paused:
            console.print("[dim]history is already not persisting in this session.[/dim]")
            return True
        console.print(
            "[dim]history is in-memory only. "
            "Restart with OPENSRE_HISTORY_ENABLED=1 to enable persistence.[/dim]"
        )
        return True
    if paused:
        console.print("[dim]history is already not persisting in this session.[/dim]")
        return True
    console.print(
        "[dim]history backend does not support runtime persistence controls in this session.[/dim]"
    )
    return True


def _history_retention(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        console.print("[red]usage:[/red] /history retention <N>")
        return True
    try:
        n = int(args[0])
        if n < 0:
            raise ValueError
    except ValueError:
        console.print("[red]retention must be a non-negative integer[/red]")
        return True

    backend = session.prompt_history_backend
    if isinstance(backend, RedactingFileHistory):
        backend.set_max_entries(n, prune=True)
        console.print(
            f"[dim]retention cap set to {n} for this session "
            "(set OPENSRE_HISTORY_MAX_ENTRIES or interactive.history.max_entries to persist).[/dim]"
        )
        return True
    console.print(
        "[dim]retention applies only when redacting persistent history. "
        "Restart with OPENSRE_HISTORY_REDACT=1 to enable.[/dim]"
    )
    return True


def _cmd_history(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _show_history(console)

    sub = args[0].lower()
    if sub == "clear":
        return _history_clear(session, console)
    if sub == "off":
        return _history_pause(session, console, paused=True)
    if sub == "on":
        return _history_pause(session, console, paused=False)
    if sub == "retention":
        return _history_retention(session, console, args[1:])

    console.print("[red]usage:[/red] /history [clear|off|on|retention <N>]")
    return True


def _cmd_privacy(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    backend = session.prompt_history_backend
    table = Table(title="Privacy settings", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("setting", style="bold")
    table.add_column("value")

    if isinstance(backend, RedactingFileHistory):
        persistence = "off (paused)" if backend.paused else "on"
        redaction = "on"
        retention = str(backend.max_entries) if backend.max_entries > 0 else "unlimited"
    elif backend is None or isinstance(backend, InMemoryHistory):
        persistence = "off (in-memory only)"
        redaction = "n/a"
        retention = "n/a"
    elif isinstance(backend, FileHistory):
        persistence = "on (no redaction)"
        redaction = "off"
        retention = "n/a"
    else:
        persistence = "unknown"
        redaction = "unknown"
        retention = "n/a"

    table.add_row("persistence", persistence)
    table.add_row("redaction", redaction)
    table.add_row("retention cap", retention)
    table.add_row("file", str(prompt_history_path()))
    table.add_row("built-in patterns", str(len(DEFAULT_REDACTION_RULES)))
    console.print(table)
    console.print(
        "[dim]threat model: prompt history is stored unencrypted on disk. "
        "Use /history clear after sharing your machine, /history off to pause this session, "
        "or set OPENSRE_HISTORY_ENABLED=0 to disable persistence entirely.[/dim]"
    )
    return True


_HISTORY_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("clear", "delete persisted history file"),
    ("off", "pause history persistence for this session"),
    ("on", "resume history persistence for this session"),
    ("retention", "set max entries cap (e.g. /history retention 1000)"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/history",
        "show command history; subcommands: clear | off | on | retention <N>",
        _cmd_history,
        first_arg_completions=_HISTORY_FIRST_ARGS,
    ),
    SlashCommand(
        "/privacy",
        "show history persistence + redaction status and threat model",
        _cmd_privacy,
    ),
]

__all__ = ["COMMANDS"]
