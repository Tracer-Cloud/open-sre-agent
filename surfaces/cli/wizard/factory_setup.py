"""Factory-style first-run setup: GitHub sign-in, then LLM, then the shell.

Kept separate from :mod:`surfaces.cli.wizard.flow` so ``opensre onboard`` stays
LLM-only while installers launch this leaner path via ``opensre setup``.
"""

from __future__ import annotations

from rich.markup import escape

from infrastructure.terminal.theme import (
    DEVICE_CODE,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    SECONDARY,
)
from surfaces.cli.wizard.components import Choice, choose, confirm, console, step_header
from surfaces.cli.wizard.flow import run_llm_setup
from surfaces.cli.wizard.summaries import render_factory_setup_header

FACTORY_SETUP_TOTAL_STEPS = 3


def _show_device_code(code: object) -> None:
    verification_uri = str(getattr(code, "verification_uri", "") or "")
    user_code = escape(str(getattr(code, "user_code", "") or ""))
    console.print()
    console.print(f"  1. Your browser will open [bold]{verification_uri}[/]")
    console.print(f"     [{SECONDARY}](if it doesn't open, visit that URL yourself).[/]")
    console.print(f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]")
    console.print("  3. Approve the request for OpenSRE.")
    console.print()
    console.print(f"  [{SECONDARY}]Waiting for you to approve in the browser…[/]")


def _run_github_signup_step(*, step: int, total_steps: int) -> bool:
    """Sign in with GitHub via device flow. Returns False when the user cancels."""
    from integrations.github import (
        GitHubDeviceFlowError,
        authenticate_and_configure_github,
        saved_github_username,
    )

    step_header(step, total_steps, "GitHub")
    existing = saved_github_username()
    if existing:
        console.print(f"Signed in to GitHub as [bold]{escape(existing)}[/]")
        if not confirm("Sign in with a different GitHub account?", default=False):
            return True

    while True:
        console.print()
        console.print("Sign in to GitHub in your browser (required for setup):")
        console.print(f"[{SECONDARY}]Requesting a one-time code from GitHub…[/]")
        try:
            result = authenticate_and_configure_github(on_prompt=_show_device_code)
        except GitHubDeviceFlowError as err:
            console.print(f"[{ERROR}]  {GLYPH_ERROR}  GitHub sign-in failed: {escape(str(err))}[/]")
            action = choose(
                "GitHub sign-in failed. What next?",
                [
                    Choice(value="retry", label="Try again", hint=None),
                    Choice(value="cancel", label="Cancel setup", hint=None),
                ],
                default="retry",
            )
            if action == "cancel":
                return False
            continue
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print(f"[{ERROR}]  {GLYPH_ERROR}  Setup cancelled.[/]")
            return False
        except Exception as err:
            console.print(f"[{ERROR}]  {GLYPH_ERROR}  GitHub sign-in failed: {escape(str(err))}[/]")
            action = choose(
                "GitHub sign-in failed. What next?",
                [
                    Choice(value="retry", label="Try again", hint=None),
                    Choice(value="cancel", label="Cancel setup", hint=None),
                ],
                default="retry",
            )
            if action == "cancel":
                return False
            continue

        if result.ok:
            who = escape(result.username) if result.username else "GitHub"
            console.print(f"[bold]{GLYPH_SUCCESS} Signed in as {who}.[/]")
            return True

        detail = escape(result.detail or "validation failed")
        console.print(f"[{ERROR}]  {GLYPH_ERROR}  GitHub could not be configured: {detail}[/]")
        action = choose(
            "GitHub setup did not finish. What next?",
            [
                Choice(value="retry", label="Try again", hint=None),
                Choice(value="cancel", label="Cancel setup", hint=None),
            ],
            default="retry",
        )
        if action == "cancel":
            return False


def run_factory_setup(_argv: list[str] | None = None) -> int:
    """Run GitHub sign-in, then LLM setup. Callers launch the shell on success."""
    render_factory_setup_header()
    if not _run_github_signup_step(step=1, total_steps=FACTORY_SETUP_TOTAL_STEPS):
        return 1
    return run_llm_setup(
        show_header=False,
        start_step=2,
        total_steps=FACTORY_SETUP_TOTAL_STEPS,
    )
