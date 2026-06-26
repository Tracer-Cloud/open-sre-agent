"""Slash commands for session-local background investigation mode."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    print_repl_table,
    repl_table,
)

_ALLOWED_NOTIFY_CHANNELS = ("email",)
_BACKGROUND_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable background investigation mode"),
    ("off", "disable background investigation mode"),
    ("status", "show current background-mode state"),
    ("list", "list tracked background investigations"),
    ("show", "show one background investigation summary"),
    ("use", "promote a completed background RCA into active follow-up context"),
    ("notify", "show or update background notification channels"),
)


def _render_background_status(session: ReplSession, console: Console) -> None:
    table = repl_table(title="Background mode\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("enabled", "yes" if session.background_mode_enabled else "no")
    table.add_row("tracked jobs", str(len(session.background_investigations)))
    table.add_row(
        "notify channels",
        ", ".join(session.background_notification_preferences.channels) or "none",
    )
    print_repl_table(console, table)


def _cmd_background(session: ReplSession, console: Console, args: list[str]) -> bool:
    sub = (args[0].lower() if args else "status").strip()

    if sub == "on":
        session.background_mode_enabled = True
        console.print(f"[{HIGHLIGHT}]background mode enabled[/]")
        return True
    if sub == "off":
        session.background_mode_enabled = False
        console.print(f"[{DIM}]background mode disabled[/]")
        return True
    if sub == "status":
        _render_background_status(session, console)
        return True
    if sub == "list":
        if not session.background_investigations:
            console.print(f"[{DIM}]no background investigations tracked in this session.[/]")
            return True
        table = repl_table(title="Background investigations\n", title_style=BOLD_BRAND)
        table.add_column("id", style="bold")
        table.add_column("status")
        table.add_column("command")
        table.add_column("root cause", overflow="fold")
        for task_id, tracked_record in session.background_investigations.items():
            table.add_row(
                task_id,
                tracked_record.status,
                tracked_record.command,
                escape(tracked_record.root_cause or "—"),
            )
        print_repl_table(console, table)
        return True
    if sub == "show":
        if len(args) < 2:
            console.print(f"[{ERROR}]usage:[/] /background show <task_id>")
            session.mark_latest(ok=False, kind="slash")
            return True
        task_id = args[1]
        selected_record = session.background_investigations.get(task_id)
        if selected_record is None:
            console.print(f"[{ERROR}]unknown background task:[/] {escape(task_id)}")
            session.mark_latest(ok=False, kind="slash")
            return True
        table = repl_table(
            title=f"Background investigation: {task_id}\n",
            title_style=BOLD_BRAND,
            show_header=False,
        )
        table.add_column("key", style="bold")
        table.add_column("value", overflow="fold")
        table.add_row("status", selected_record.status)
        table.add_row("command", selected_record.command)
        table.add_row("root cause", escape(selected_record.root_cause or "—"))
        table.add_row(
            "top analysis",
            escape(
                "; ".join(selected_record.top_analysis) if selected_record.top_analysis else "—"
            ),
        )
        table.add_row(
            "next steps",
            escape("; ".join(selected_record.next_steps) if selected_record.next_steps else "—"),
        )
        table.add_row(
            "notify",
            escape(
                ", ".join(f"{k}:{v}" for k, v in selected_record.notification_results.items())
                or "—"
            ),
        )
        print_repl_table(console, table)
        return True
    if sub == "use":
        if len(args) < 2:
            console.print(f"[{ERROR}]usage:[/] /background use <task_id>")
            session.mark_latest(ok=False, kind="slash")
            return True
        task_id = args[1]
        selected_record = session.background_investigations.get(task_id)
        if selected_record is None:
            console.print(f"[{ERROR}]unknown background task:[/] {escape(task_id)}")
            session.mark_latest(ok=False, kind="slash")
            return True
        if not selected_record.final_state:
            console.print(
                f"[{ERROR}]background task has no completed RCA state yet:[/] {escape(task_id)}"
            )
            session.mark_latest(ok=False, kind="slash")
            return True
        session.last_state = dict(selected_record.final_state)
        session.accumulate_from_state(selected_record.final_state)
        console.print(
            f"[{HIGHLIGHT}]background RCA active[/] "
            f"[{DIM}]— follow-up context now points to {escape(task_id)}.[/]"
        )
        return True
    if sub == "notify":
        action = (args[1].lower() if len(args) > 1 else "list").strip()
        if action == "list":
            console.print(
                f"[{DIM}]background notify channels:[/] "
                f"{', '.join(session.background_notification_preferences.channels) or 'none'}"
            )
            return True
        if action == "set":
            if len(args) < 3:
                console.print(f"[{ERROR}]usage:[/] /background notify set <channel[,channel...]>")
                session.mark_latest(ok=False, kind="slash")
                return True
            requested = [part.strip().lower() for part in args[2].split(",") if part.strip()]
            invalid = [part for part in requested if part not in _ALLOWED_NOTIFY_CHANNELS]
            if invalid:
                console.print(
                    f"[{ERROR}]invalid channel(s):[/] {escape(', '.join(invalid))} "
                    f"[{DIM}](allowed: {', '.join(_ALLOWED_NOTIFY_CHANNELS)})[/]"
                )
                session.mark_latest(ok=False, kind="slash")
                return True
            session.background_notification_preferences.set_channels(requested)
            console.print(
                f"[{HIGHLIGHT}]background notify channels set:[/] "
                f"{', '.join(session.background_notification_preferences.channels)}"
            )
            return True
        console.print(f"[{ERROR}]unknown notify subcommand:[/] {escape(action)}")
        session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/background status[/bold], [bold]/background list[/bold], "
        "[bold]/background show <task_id>[/bold], or [bold]/background notify list[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/background",
        "Manage background investigation mode and completed RCA summaries.",
        _cmd_background,
        usage=(
            "/background on",
            "/background off",
            "/background status",
            "/background list",
            "/background show <task_id>",
            "/background use <task_id>",
            "/background notify list",
            "/background notify set <channel[,channel...]>",
        ),
        first_arg_completions=_BACKGROUND_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    )
]

__all__ = ["COMMANDS"]
