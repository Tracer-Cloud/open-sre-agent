"""Forced sign-in screen for the interactive shell.

Shown in place of the composer when the user is not signed in: the launch
banner, a welcome box, and a Login/Exit menu. Authentication is injected — this
module owns only the presentation and the choice loop, not the login flow — so
the web-app sign-in can plug into the ``is_signed_in`` / ``login`` seams without
this screen depending on it.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from config.constants import (
    SIGN_IN_PROMPT,
    WELCOME_DESCRIPTION,
    WELCOME_TITLE,
)
from infrastructure.terminal.theme import DIM, HIGHLIGHT, SECONDARY, TEXT
from surfaces.shared.terminal.banner.banner import build_launch_banner
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive

_WELCOME_BOX_WIDTH = 76


class SignInChoice(enum.StrEnum):
    """The two actions offered on the forced sign-in screen."""

    LOGIN = "Login"
    EXIT = "Exit"


def build_welcome_box() -> RenderableType:
    """The bordered welcome box: blue title over the one-line product description."""
    body = Text()
    body.append(WELCOME_TITLE, style=f"bold {HIGHLIGHT}")
    body.append("\n")
    body.append(WELCOME_DESCRIPTION, style=str(TEXT))
    return Panel(body, box=ROUNDED, border_style=str(DIM), padding=(1, 2), width=_WELCOME_BOX_WIDTH)


def render_sign_in_screen(console: Console) -> None:
    """Paint the launch banner, the welcome box, and the sign-in prompt line."""
    console.print(
        Group(
            build_launch_banner(console),
            build_welcome_box(),
            Text(),
            Text(SIGN_IN_PROMPT, style=str(SECONDARY)),
        )
    )


def prompt_login_or_exit() -> SignInChoice | None:
    """Show the Login/Exit menu; return the choice, or ``None`` on Esc."""
    picked = repl_choose_one(
        title=SIGN_IN_PROMPT,
        choices=[(SignInChoice.LOGIN, SignInChoice.LOGIN), (SignInChoice.EXIT, SignInChoice.EXIT)],
    )
    if picked == SignInChoice.LOGIN:
        return SignInChoice.LOGIN
    if picked == SignInChoice.EXIT:
        return SignInChoice.EXIT
    return None


def run_sign_in_gate(
    console: Console,
    *,
    is_signed_in: Callable[[], bool],
    login: Callable[[], bool],
) -> bool:
    """Gate the REPL behind sign-in; return ``True`` to proceed, ``False`` to exit.

    Returns immediately when already signed in. Otherwise renders the sign-in
    screen and loops the Login/Exit menu: ``Login`` runs the injected ``login``
    (retrying on failure), ``Exit`` or Esc declines. On a non-interactive stdin
    the gate cannot prompt, so it proceeds without forcing sign-in.
    """
    if is_signed_in():
        return True
    if not repl_tty_interactive():
        return True
    render_sign_in_screen(console)
    while True:
        choice = prompt_login_or_exit()
        if choice is SignInChoice.LOGIN:
            if login():
                return True
            continue  # login failed — offer the choice again
        return False  # Exit or Esc


__all__ = [
    "SignInChoice",
    "build_welcome_box",
    "prompt_login_or_exit",
    "render_sign_in_screen",
    "run_sign_in_gate",
]
