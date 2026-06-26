"""Slash command: interactive theme selection and persistence."""

from __future__ import annotations

import time

from rich.console import Console

from cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.ui import theme as ui_theme
from cli.interactive_shell.ui.choice_menu import repl_choose_one, repl_tty_interactive
from cli.interactive_shell.ui.theme import (
    get_active_theme_name,
    list_theme_names,
    set_active_theme,
)


def _refresh_prompt_style(session: ReplSession) -> None:
    """Defer prompt-toolkit style refresh until the next prompt_async turn."""
    session.pending_theme_refresh = True


def _settle_and_drain_cpr() -> None:
    """Let in-flight terminal CPR replies land, then discard them from stdin."""
    from cli.interactive_shell.runtime.cpr_stdin import drain_stale_cpr_bytes

    time.sleep(0.05)
    drain_stale_cpr_bytes()


def _persist_and_report_theme(
    session: ReplSession,
    console: Console,
    selected: str,
) -> None:
    from cli.commands.config import _load_config, _save_config, _set_nested_key
    from cli.interactive_shell.ui.rendering import refresh_welcome_poster

    active = set_active_theme(selected)
    session.active_theme_name = active.name

    updated = _set_nested_key(_load_config(), "interactive.theme", active.name)
    _save_config(updated)

    # Poster redraw and prompt invalidation both trigger prompt_toolkit DSR/CPR
    # queries under patch_stdout. Drain between each step so bytes never leak into
    # the next prompt buffer (e.g. ``^[[1;1Rtheme set: pink``).
    _settle_and_drain_cpr()
    refresh_welcome_poster(console, session=session, theme_notice=active.name)
    _settle_and_drain_cpr()
    _refresh_prompt_style(session)


def _cmd_theme(session: ReplSession, console: Console, args: list[str]) -> bool:
    if args:
        selected = args[0].strip().lower()
        if selected not in list_theme_names():
            supported = ", ".join(list_theme_names())
            console.print(f"[{ui_theme.ERROR}]unknown theme:[/] {selected}  (choose: {supported})")
            return True
        _persist_and_report_theme(session, console, selected)
        return True

    if not repl_tty_interactive():
        console.print(f"[{ui_theme.DIM}]/theme requires an interactive TTY session.[/]")
        return True

    current = get_active_theme_name()
    session.active_theme_name = current
    choices = [
        (name, f"{name}{' (current)' if name == current else ''}") for name in list_theme_names()
    ]
    picked = repl_choose_one(
        title="theme",
        breadcrumb="/theme",
        choices=choices,
        initial_value=current,
    )
    if picked is None:
        console.print(f"[{ui_theme.DIM}]theme unchanged.[/]")
        return True

    _persist_and_report_theme(session, console, picked)
    return True


_THEME_FIRST_ARGS: tuple[tuple[str, str], ...] = tuple(
    (name, "interactive palette") for name in list_theme_names()
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/theme",
        "Choose and persist the interactive shell color theme.",
        _cmd_theme,
        usage=("/theme", "/theme <name>"),
        examples=("/theme blue", "/theme green"),
        first_arg_completions=_THEME_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    )
]

__all__ = ["COMMANDS"]
