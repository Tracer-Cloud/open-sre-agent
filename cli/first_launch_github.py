"""First-launch mandatory GitHub login gate.

On the first interactive launch of ``opensre`` on macOS or Windows (never on
Linux, never in CI/tests), the user must sign in to GitHub via device flow. The
sign-in runs the hosted GitHub MCP setup, persists the integration, and
propagates the authenticated GitHub username to PostHog.

Escape hatch: ``OPENSRE_SKIP_GITHUB_LOGIN=1`` bypasses the gate so a GitHub
outage or a disabled device flow can never permanently lock anyone out. The gate
is also auto-bypassed on Linux and in CI/test environments.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

import platform
from cli.interactive_shell.ui import repl_tty_interactive
from cli.interactive_shell.ui.theme import DEVICE_CODE
from platform.analytics.cli import capture_github_login_completed, identify_github_username
from platform.analytics.source import is_test_run

_SKIP_ENV_VAR = "OPENSRE_SKIP_GITHUB_LOGIN"
_ELIGIBLE_OS = frozenset({"Darwin", "Windows"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _skip_requested() -> bool:
    return os.getenv(_SKIP_ENV_VAR, "").strip().lower() in _TRUTHY


def _eligible_os() -> bool:
    return platform.system() in _ELIGIBLE_OS


def _github_already_configured() -> bool:
    from integrations.github_mcp import github_mcp_config_from_env
    from integrations.store import get_integration

    if get_integration("github") is not None:
        return True
    try:
        return github_mcp_config_from_env() is not None
    except Exception:
        return False


def should_require_github_login() -> bool:
    """Return True when the mandatory first-launch GitHub login must run now."""
    if _skip_requested():
        return False
    if not _eligible_os():
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


def _propagate_username(username: str) -> None:
    if not username:
        return
    identify_github_username(username)
    capture_github_login_completed(username)


def _print_intro(console: Console) -> None:
    console.print()
    console.print("[bold]Connect GitHub to get started[/bold]")
    console.print(
        "OpenSRE needs read access to your GitHub repositories to investigate "
        "incidents against your source. Sign in once with your browser."
    )
    console.print(
        f"[dim](Set {_SKIP_ENV_VAR}=1 to skip this — e.g. if GitHub sign-in is unavailable.)[/dim]"
    )


def _show_device_code(console: Console, code: object) -> None:
    from integrations.github_mcp_oauth import GitHubDeviceCode

    if not isinstance(code, GitHubDeviceCode):
        return
    user_code = escape(code.user_code)
    console.print()
    console.print(f"  1. Your browser will open [underline]{code.verification_uri}[/underline]")
    console.print("     (if it doesn't open automatically, visit that URL yourself).")
    console.print(f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]")
    console.print("  3. Approve the request for OpenSRE.")
    console.print()
    console.print("  [dim]Waiting for you to approve in the browser… (Ctrl-C to cancel)[/dim]")


def _print_quit_guidance(console: Console) -> None:
    console.print()
    console.print(
        "GitHub sign-in is required to use OpenSRE. You can try again by relaunching "
        f"[bold]opensre[/bold], or set [bold]{_SKIP_ENV_VAR}=1[/bold] to bypass this step."
    )


def _ask_retry(_console: Console) -> bool:
    import questionary

    try:
        answer = questionary.confirm("Try GitHub sign-in again?", default=True).ask()
    except (EOFError, KeyboardInterrupt):
        return False
    return bool(answer)


def _attempt_login(console: Console) -> str:
    """Run one login attempt. Returns ``"success"``, ``"failed"``, or ``"quit"``."""
    from integrations.github_login import authenticate_and_configure_github
    from integrations.github_mcp_oauth import GitHubDeviceFlowError

    try:
        result = authenticate_and_configure_github(
            on_prompt=lambda code: _show_device_code(console, code),
        )
    except (EOFError, KeyboardInterrupt):
        console.print("\nCancelled.")
        return "quit"
    except GitHubDeviceFlowError as err:
        console.print(f"[yellow]GitHub sign-in is unavailable:[/yellow] {err}")
        return "failed"
    except Exception as err:  # network/transport issues
        console.print(f"[yellow]GitHub sign-in failed:[/yellow] {err}")
        return "failed"

    if result.ok:
        # Persisting the GitHub integration (done inside
        # ``authenticate_and_configure_github``) is what suppresses the gate on
        # subsequent launches — there is no separate completion marker to write.
        _propagate_username(result.username)
        who = f"@{result.username}" if result.username else "your GitHub account"
        console.print(f"[bold]Connected.[/bold] Signed in as {who}.")
        return "success"

    console.print(f"[yellow]Could not verify GitHub access:[/yellow] {result.detail}")
    return "failed"


def require_github_login_on_first_launch(console: Console | None = None) -> bool:
    """Run the mandatory first-launch GitHub login.

    Returns True when the caller should proceed into the REPL (login succeeded),
    and False when the user chose to quit (the caller should exit without
    starting the REPL).
    """
    con = console or Console(highlight=False)
    _print_intro(con)
    while True:
        outcome = _attempt_login(con)
        if outcome == "success":
            return True
        if outcome == "quit" or not _ask_retry(con):
            _print_quit_guidance(con)
            return False
