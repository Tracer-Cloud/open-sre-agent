"""Forced sign-in screen for the interactive shell.

Shown in place of the composer when the user is not signed in: the launch
banner, a welcome box, and a Login/Exit menu. Authentication is injected — this
module owns only the presentation and the choice loop, not the login flow — so
the web-app sign-in can plug into the ``is_signed_in`` / ``login`` seams without
this screen depending on it.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Callable

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from config.constants import (
    FORCE_SIGN_IN_ENV,
    SIGN_IN_PROMPT,
    WELCOME_DESCRIPTION,
    WELCOME_TITLE,
)
from infrastructure.terminal.theme import DIM, HIGHLIGHT, SECONDARY, TEXT
from surfaces.shared.terminal.banner import animate_launch_wordmark, build_launch_banner
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive

_TRUTHY = {"1", "true", "yes", "on"}


class SignInChoice(enum.StrEnum):
    """The two actions offered on the forced sign-in screen."""

    LOGIN = "Login"
    EXIT = "Exit"


def forced_sign_in_enabled() -> bool:
    """Whether the shell should show the sign-in screen on startup (opt-in)."""
    return os.environ.get(FORCE_SIGN_IN_ENV, "").strip().lower() in _TRUTHY


def build_welcome_box() -> RenderableType:
    """The bordered welcome box: blue title over the one-line product description."""
    body = Text()
    body.append(WELCOME_TITLE, style=f"bold {HIGHLIGHT}")
    body.append("\n")
    body.append(WELCOME_DESCRIPTION, style=str(TEXT))
    # No fixed width: the panel expands to the full terminal width.
    return Panel(body, box=ROUNDED, border_style=str(DIM), padding=(1, 2))


def render_sign_in_screen(console: Console) -> None:
    """Paint the launch banner, the welcome box, and the sign-in prompt line."""
    screen = Group(
        build_launch_banner(console),
        build_welcome_box(),
        Text(),
        Text(SIGN_IN_PROMPT, style=str(SECONDARY)),
    )
    animate_launch_wordmark(console)
    console.print(screen)


def prompt_login_or_exit() -> SignInChoice | None:
    """Show the Login/Exit menu; return the choice, or ``None`` on Esc.

    The sign-in prompt is already printed by ``render_sign_in_screen`` above the
    menu, so the menu itself carries no title (avoids repeating the prompt).
    """
    picked = repl_choose_one(
        title="",
        choices=[(SignInChoice.LOGIN, SignInChoice.LOGIN), (SignInChoice.EXIT, SignInChoice.EXIT)],
        numbered=False,
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
    "forced_sign_in_enabled",
    "prompt_login_or_exit",
    "render_sign_in_screen",
    "run_sign_in_gate",
]
