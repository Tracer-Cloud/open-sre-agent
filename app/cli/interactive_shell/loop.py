"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal."""

from __future__ import annotations

import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.agent_actions import execute_cli_actions
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up
from app.cli.interactive_shell.router import classify_input
from app.cli.interactive_shell.session import ReplSession


def _run_new_alert(text: str, session: ReplSession, console: Console) -> None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.cli.investigation import run_investigation_for_session

    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
        )
    except KeyboardInterrupt:
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        # Exception repr may contain brackets (stack frame refs, config
        # dicts) that Rich would eat as markup tags — escape before printing.
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)


async def _run_one_turn(
    prompt: PromptSession[str],
    session: ReplSession,
    console: Console,
) -> bool:
    """Read one line of input and dispatch. Returns False to exit."""
    try:
        text = await prompt.prompt_async(ANSI("\x1b[1;36m› \x1b[0m"))
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False

    text = text.strip()
    if not text:
        return True

    kind = classify_input(text, session)
    if kind == "slash":
        # Rewrite bare-word commands to their slash form before dispatch.
        cmd_text = text if text.startswith("/") else f"/{text}"
        session.record("slash", cmd_text)
        return dispatch_slash(cmd_text, session, console)

    if kind == "cli_help":
        answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return True

    if kind == "cli_agent":
        if execute_cli_actions(text, session, console):
            return True
        answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        return True

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        return True

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console)
    session.record("follow_up", text)
    return True


async def _repl_main(initial_input: str | None = None, config: ReplConfig | None = None) -> int:  # noqa: ARG001
    # force_terminal + truecolor so Rich always emits full ANSI, even after
    # prompt_toolkit has claimed and released stdout for input handling.
    # Without this, slash-command output after the first prompt renders as
    # literal escape codes in some terminal emulators.
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    session = ReplSession()
    prompt: PromptSession[str] = PromptSession()

    # Allow a single pre-seeded input for test harnesses
    if initial_input:
        for line in initial_input.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            kind = classify_input(stripped, session)
            if kind == "slash":
                cmd_text = stripped if stripped.startswith("/") else f"/{stripped}"
                session.record("slash", cmd_text)
                if not dispatch_slash(cmd_text, session, console):
                    return 0
            elif kind == "cli_help":
                answer_cli_help(stripped, session, console)
                session.record("cli_help", stripped)
            elif kind == "cli_agent":
                if not execute_cli_actions(stripped, session, console):
                    answer_cli_agent(stripped, session, console)
                    session.record("cli_agent", stripped)
            elif kind == "new_alert":
                _run_new_alert(stripped, session, console)
            else:
                answer_follow_up(stripped, session, console)
                session.record("follow_up", stripped)

    while True:
        should_continue = await _run_one_turn(prompt, session, console)
        if not should_continue:
            return 0


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    """Enter the interactive REPL. Returns the exit code."""
    cfg = config or ReplConfig.load()

    if not cfg.enabled:
        return 0

    if not sys.stdin.isatty() and initial_input is None:
        # In non-TTY contexts (piped input, CI), don't start an interactive loop.
        # Callers should use `opensre investigate` instead.
        return 0

    try:
        return asyncio.run(_repl_main(initial_input=initial_input, config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl"]
