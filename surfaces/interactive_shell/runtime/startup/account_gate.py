"""GitHub-account seams for the interactive-shell sign-in gate.

The screen and Login/Exit loop live in ``surfaces.interactive_shell.ui.sign_in``.
This module injects the first-launch GitHub identity: whether a username is
already saved, and the device-flow login that persists one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrastructure.analytics.source import is_test_run

if TYPE_CHECKING:
    from rich.console import Console


def account_is_signed_in() -> bool:
    """True when a GitHub username is already saved on this machine."""
    from integrations.github import saved_github_username

    return bool(saved_github_username())


def account_login(*, console: Console | None = None) -> bool:
    """Run GitHub device-flow login; return True when a username is persisted."""
    from rich.markup import escape

    from infrastructure.terminal.theme import DEVICE_CODE, ERROR, SECONDARY
    from integrations.github import authenticate_and_configure_github

    def _show_device_code(code: object) -> None:
        if console is None:
            return
        verification_uri = escape(str(getattr(code, "verification_uri", "") or ""))
        user_code = escape(str(getattr(code, "user_code", "") or ""))
        console.print()
        console.print(f"  1. Your browser will open [bold]{verification_uri}[/]")
        console.print(f"     [{SECONDARY}](if it doesn't open, visit that URL yourself).[/]")
        console.print(
            f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]"
        )
        console.print("  3. Approve the request for OpenSRE.")
        console.print()
        console.print(f"  [{SECONDARY}]Waiting for you to approve in the browser…[/]")

    try:
        result = authenticate_and_configure_github(on_prompt=_show_device_code)
    except Exception as err:
        if console is not None:
            console.print(f"[{ERROR}]GitHub sign-in failed: {escape(str(err))}[/]")
        return False
    return result.ok


def should_paint_launch_banner() -> bool:
    """True when the sign-in gate will not paint the launch banner itself.

    With the opt-in flag on, the sign-in screen paints the banner on an
    interactive TTY, so only a non-interactive start (and disabled opt-in / test
    runs) still needs the standalone banner and composer chrome.
    """
    if is_test_run():
        return True
    from surfaces.interactive_shell.ui.sign_in import forced_sign_in_enabled
    from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive

    if not forced_sign_in_enabled():
        return True
    return not repl_tty_interactive()


def pass_sign_in_gate(console: Console) -> bool:
    """Run the sign-in gate; return True to proceed into the REPL.

    Test processes skip the prompt (same reason as the loops picker) so pytest
    on a TTY cannot hang on Login/Exit. The screen itself is opt-in via
    ``OPENSRE_FORCE_SIGNIN`` until web-app auth is the default gate.
    """
    if is_test_run():
        return True
    from surfaces.interactive_shell.ui.sign_in import (
        forced_sign_in_enabled,
        run_sign_in_gate,
    )

    if not forced_sign_in_enabled():
        return True

    def _login() -> bool:
        return account_login(console=console)

    # The opt-in flag forces the screen for testing, even when a username is
    # already saved; the web-app gate will consult ``account_is_signed_in``.
    return run_sign_in_gate(
        console,
        is_signed_in=lambda: False,
        login=_login,
    )


__all__ = [
    "account_is_signed_in",
    "account_login",
    "pass_sign_in_gate",
    "should_paint_launch_banner",
]
