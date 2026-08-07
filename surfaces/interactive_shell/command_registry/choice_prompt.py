"""Slash command: open the pending interactive selection menu (``/choose``).

The ``ask_user_choice`` action tool stores a
:class:`~core.agent_harness.session.pending_choice.PendingUserChoice` on the
session and queues this command via ``set_auto_command``, so it runs as a
literal slash turn with exclusive stdin — the only place a raw-stdin arrow-key
picker is safe (see ``_EXCLUSIVE_STDIN_MENU_COMMANDS`` in
``runtime/utils/input_policy.py``). The selected option label is auto-submitted
as the next user message so the agent receives the decision verbatim.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from platform.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.components.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_tty_interactive,
)


def _cmd_choose(session: Session, console: Console, args: list[str]) -> bool:
    del args
    pending = session.pending_user_choice
    session.pending_user_choice = None
    if pending is None:
        console.print(f"[{ui_theme.DIM}]No selection menu is pending.[/]")
        return True

    if not repl_tty_interactive():
        # Non-TTY fallback: show the options; the user replies in plain text.
        print_valid_choice_list(
            console,
            title=escape(pending.title),
            choices=list(pending.options),
        )
        console.print(f"[{ui_theme.DIM}]Reply with the option you want.[/]")
        return True

    picked = repl_choose_one(
        title=pending.title,
        choices=[(option, option) for option in pending.options],
    )
    if picked is None:
        console.print(f"[{ui_theme.DIM}]Selection cancelled — type a reply instead.[/]")
        return True

    # Auto-submit the chosen label as the next user message; the prompt echo is
    # the confirmation, so nothing is printed here.
    session.terminal.set_auto_command(picked)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/choose",
        "Open the pending interactive selection menu queued by the agent.",
        _cmd_choose,
        usage=("/choose",),
    )
]

__all__ = ["COMMANDS"]
