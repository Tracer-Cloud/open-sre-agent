"""First-launch GitHub login gate.

On the first interactive launch of ``opensre`` (all platforms), the user is
prompted to sign in to GitHub via device flow unless they are in CI/CD, a test
harness, or a non-interactive session.

An offline A/B test splits installs into:

* ``control`` — skip is allowed (menu choice + Escape during wait defer the gate)
* ``forced`` — skip is removed; abandoning the gate aborts REPL startup

Variant assignment is sticky per install anonymous id (see
``platform.analytics.cli.resolve_github_gate_variant``). Override with
``OPENSRE_GITHUB_GATE_VARIANT=control|forced`` for local testing.

Escape hatch: ``OPENSRE_SKIP_GITHUB_LOGIN=1`` bypasses the gate so a GitHub
outage or a disabled device flow can never permanently lock anyone out. The gate
is also auto-bypassed in CI/test environments and when stdin is not a TTY.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from rich.console import Console
from rich.markup import escape

from config.repl_config import read_github_login_deferred, write_github_login_deferred
from platform.analytics.cli import (
    GITHUB_GATE_VARIANT_CONTROL,
    capture_github_login_abandoned,
    capture_github_login_completed,
    capture_github_login_prompted,
    capture_github_login_skipped,
    resolve_github_gate_variant,
    stamp_github_gate_variant,
)
from platform.analytics.source import is_test_run
from platform.terminal.theme import DEVICE_CODE
from surfaces.interactive_shell.ui import repl_tty_interactive

_SKIP_ENV_VAR = "OPENSRE_SKIP_GITHUB_LOGIN"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_SIGN_IN_CHOICE = "sign_in"
_SKIP_CHOICE = "skip"
_RETRY_CHOICE = "retry"
_DECLINE_RETRY_CHOICE = "declined_retry"
_CANCEL_RETRY_CHOICE = "cancelled"


def _skip_requested() -> bool:
    return os.getenv(_SKIP_ENV_VAR, "").strip().lower() in _TRUTHY


def _github_login_explicitly_bypassed() -> bool:
    """Cheap check for contexts where gate errors should not block startup."""
    if _skip_requested():
        return True
    if os.getenv("OPENSRE_INVESTIGATION_SOURCE", "").strip().lower() == "test":
        return True
    if os.getenv("OPENSRE_IS_TEST", "0").strip() == "1":
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true":
        return True
    ci_value = os.getenv("CI", "").strip().lower()
    if ci_value in {"1", "true", "yes"}:
        return True
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _github_already_configured() -> bool:
    from integrations.github.mcp import github_integration_is_configured

    return github_integration_is_configured()


def should_require_github_login() -> bool:
    """Return True when the first-launch GitHub login prompt must run now."""
    if _skip_requested():
        return False
    if read_github_login_deferred():
        return False
    if is_test_run():
        return False
    if not repl_tty_interactive():
        return False
    # GitHub being configured is the authoritative bypass. We intentionally do
    # NOT consult a first-launch "completion" marker here: a stale marker must
    # never let the REPL start once the GitHub integration has been removed
    # (e.g. via ``/integrations remove github``). Re-checking the store is cheap,
    # so the gate always re-runs when GitHub is not currently configured.
    return not _github_already_configured()


def clear_github_login_deferral() -> None:
    """Clear a saved skip so removing GitHub can re-prompt on the next launch."""
    if not read_github_login_deferred():
        return
    write_github_login_deferred(False)


def _propagate_username(username: str, *, variant: str) -> None:
    if not username:
        return
    # ``authenticate_and_configure_github`` already calls identify_github_username;
    # only emit the one-time login lifecycle event here.
    capture_github_login_completed(username, variant=variant)


def _print_intro(console: Console, *, allow_skip: bool) -> None:
    console.print()
    console.print("[bold]Connect GitHub to get started[/bold]")
    console.print(
        "OpenSRE needs read access to your GitHub repositories to investigate "
        "incidents against your source. Sign in once with your browser."
    )
    if allow_skip:
        console.print(
            "[dim](Escape to skip for now, or set "
            f"{_SKIP_ENV_VAR}=1 if GitHub sign-in is unavailable.)[/dim]"
        )
    else:
        console.print(
            "[dim](Sign-in is required to continue. Set "
            f"{_SKIP_ENV_VAR}=1 only if GitHub sign-in is unavailable.)[/dim]"
        )


def _show_device_code(console: Console, code: object, *, allow_skip: bool) -> None:
    from integrations.github.mcp_oauth import GitHubDeviceCode

    if not isinstance(code, GitHubDeviceCode):
        return
    user_code = escape(code.user_code)
    console.print()
    console.print(f"  1. Your browser will open [underline]{code.verification_uri}[/underline]")
    console.print("     (if it doesn't open automatically, visit that URL yourself).")
    console.print(f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]")
    console.print("  3. Approve the request for OpenSRE.")
    console.print()
    if allow_skip:
        console.print(
            "  [dim]Waiting for you to approve in the browser… (Escape or Ctrl-C to skip)[/dim]"
        )
    else:
        console.print("  [dim]Waiting for you to approve in the browser…[/dim]")


def _print_skip_guidance(console: Console) -> None:
    console.print()
    console.print(
        "[dim]Skipped GitHub sign-in. Connect later with "
        "[bold]/integrations setup[/bold] or [bold]/mcp connect github[/bold].[/dim]"
    )


def _print_forced_abort_guidance(console: Console) -> None:
    console.print()
    console.print(
        "GitHub sign-in is required to continue. "
        f"Set [bold]{_SKIP_ENV_VAR}=1[/bold] only if sign-in is unavailable, "
        "then relaunch [bold]opensre[/bold]."
    )


def _defer_github_login() -> None:
    write_github_login_deferred(True)


def _sleep_until_or_cancel(seconds: float) -> None:
    """Sleep up to ``seconds``, raising ``KeyboardInterrupt`` when the user skips."""
    if seconds <= 0 or not sys.stdin.isatty():
        time.sleep(seconds)
        return

    if os.name == "nt":
        import msvcrt

        from surfaces.interactive_shell.ui.components.key_reader import read_key_windows

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if msvcrt.kbhit() and read_key_windows() == "cancel":  # type: ignore[attr-defined]
                raise KeyboardInterrupt
            time.sleep(0.05)
        return

    import select
    import termios
    import tty

    from surfaces.interactive_shell.ui.components.key_reader import read_key_unix

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.15))
            if not ready:
                continue
            if read_key_unix() == "cancel":
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)  # type: ignore[attr-defined]


def _offer_github_login(_console: Console, *, allow_skip: bool) -> bool:
    """Return True when the user wants to start browser sign-in.

    When ``allow_skip`` is False (forced variant), there is no skip choice — the
    gate proceeds straight into device-flow sign-in.
    """
    if not allow_skip:
        return True

    import questionary

    try:
        choice = questionary.select(
            "Connect GitHub now?",
            choices=[
                questionary.Choice(
                    "Sign in with GitHub (opens browser)",
                    value=_SIGN_IN_CHOICE,
                ),
                questionary.Choice("Skip for now", value=_SKIP_CHOICE),
            ],
            default=_SIGN_IN_CHOICE,
        ).ask()
    except (EOFError, KeyboardInterrupt):
        return False
    if choice is None:
        return False
    return bool(choice == _SIGN_IN_CHOICE)


def _ask_retry(_console: Console) -> str:
    import questionary

    try:
        answer = questionary.confirm("Try GitHub sign-in again?", default=True).ask()
    except (EOFError, KeyboardInterrupt):
        return _CANCEL_RETRY_CHOICE
    if answer is None:
        return _CANCEL_RETRY_CHOICE
    return _RETRY_CHOICE if answer else _DECLINE_RETRY_CHOICE


def _attempt_login(console: Console, *, allow_skip: bool, variant: str) -> str:
    """Run one login attempt. Returns ``"success"``, ``"failed"``, or ``"skipped"``."""
    from integrations.github.login import authenticate_and_configure_github
    from integrations.github.mcp_oauth import GitHubDeviceFlowError

    try:
        result = authenticate_and_configure_github(
            on_prompt=lambda code: _show_device_code(console, code, allow_skip=allow_skip),
            poll_sleep=_sleep_until_or_cancel,
        )
    except (EOFError, KeyboardInterrupt):
        if allow_skip:
            console.print("\nSkipped GitHub sign-in.")
        else:
            console.print("\nGitHub sign-in cancelled.")
        return "skipped"
    except GitHubDeviceFlowError as err:
        console.print(f"[yellow]GitHub sign-in is unavailable:[/yellow] {err}")
        return "failed"
    except Exception as err:  # network/transport issues
        console.print(f"[yellow]GitHub sign-in failed:[/yellow] {err}")
        return "failed"

    if result.ok:
        clear_github_login_deferral()
        # Persisting the GitHub integration (done inside
        # ``authenticate_and_configure_github``) is what suppresses the gate on
        # subsequent launches — there is no separate completion marker to write.
        _propagate_username(result.username, variant=variant)
        who = f"@{result.username}" if result.username else "your GitHub account"
        console.print(f"[bold]Connected.[/bold] Signed in as {who}.")
        return "success"

    console.print(f"[yellow]Could not verify GitHub access:[/yellow] {result.detail}")
    return "failed"


def require_github_login_on_first_launch(console: Console | None = None) -> bool:
    """Run the first-launch GitHub login prompt.

    Returns True when the caller should proceed into the REPL (login succeeded, or
    the user skipped in the ``control`` variant), and False when startup must abort
    (forced-variant abandonment / decline).
    """
    con = console or Console(highlight=False)
    variant = resolve_github_gate_variant()
    stamp_github_gate_variant(variant)
    allow_skip = variant == GITHUB_GATE_VARIANT_CONTROL
    capture_github_login_prompted(variant=variant)
    _print_intro(con, allow_skip=allow_skip)

    if allow_skip and not _offer_github_login(con, allow_skip=True):
        capture_github_login_skipped(variant=variant)
        _defer_github_login()
        _print_skip_guidance(con)
        return True

    while True:
        outcome = _attempt_login(con, allow_skip=allow_skip, variant=variant)
        if outcome == "success":
            return True
        if outcome == "skipped":
            if allow_skip:
                capture_github_login_skipped(variant=variant)
                _defer_github_login()
                _print_skip_guidance(con)
                return True
            capture_github_login_abandoned(variant=variant, reason="cancelled")
            _print_forced_abort_guidance(con)
            return False
        retry_decision = _ask_retry(con)
        if retry_decision != _RETRY_CHOICE:
            if allow_skip:
                capture_github_login_skipped(variant=variant)
                _defer_github_login()
                _print_skip_guidance(con)
                return True
            capture_github_login_abandoned(variant=variant, reason=retry_decision)
            _print_forced_abort_guidance(con)
            return False


def require_startup_github_login(console: Console) -> bool:
    """Return True when startup may proceed past the GitHub login gate.

    On an unexpected gate error we deliberately do NOT fail open into the REPL:
    that would let a gate bug silently skip sign-in. Instead we only allow
    startup when an explicit, documented bypass applies.
    """
    try:
        if not should_require_github_login():
            return True
        return require_github_login_on_first_launch(console)
    except Exception:
        logging.getLogger(__name__).warning(
            "First-launch GitHub login gate failed.",
            exc_info=True,
        )
        if _github_login_explicitly_bypassed():
            return True
        console.print(
            "GitHub sign-in could not run. "
            f"Set [bold]{_SKIP_ENV_VAR}=1[/bold] to bypass this, then relaunch "
            "[bold]opensre[/bold]."
        )
        return False
