"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal.

Built around :class:`textual_repl.OpenSREApp` (#1679), a Textual-based
``App`` with a declarative React-like component tree. The input box is a
fixed widget pinned at the bottom; output flows above as a scrolling
``RichLog``; the ``StatusLine`` widget shows ``thinking… (Ns · ↓ X tokens)``
during streaming. Type-ahead works because Textual's reactive render model
keeps the input widget responsive while ``dispatch_fn`` runs on a worker
thread.

The pre-textual prompt_toolkit-only approach couldn't compose the "input
pinned + output flowing above + streaming indicator" pattern reliably —
race-induced duplicate prompts, status-line overlap, ``rich.Live``
fighting ``patch_stdout``. Textual's declarative model is what Claude
Code's Ink (React for terminals) does, just in Python.
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from app.agents.sweep import run_startup_sweep
from app.analytics.cli import capture_terminal_turn_summarized
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.agent_actions import execute_cli_actions_with_metrics
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up
from app.cli.interactive_shell.prompt_surface import render_submitted_prompt
from app.cli.interactive_shell.router import route_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import DIM, ERROR, WARNING
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception

_INTERVENTION_CORRECTION_RE = re.compile(
    r"("
    r"no(?=[,.!?]|$)"
    r"|nope\b"
    r"|nvm\b"
    r"|nevermind\b|never\s*mind\b"
    r"|wrong\b"
    r"|wait(?=[,.!?]|$)"
    r"|stop(?=[,.!?]|$)"
    r"|actually\b"
    r"|scratch\s+that\b"
    r"|instead(?=[,.!?]|$)"
    r"|(?:let'?s\s+)?do\s+[^.\n]{1,60}\s+instead\b"
    r"|try\s+[^.\n]{1,60}\s+instead\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_correction(text: str) -> bool:
    """True when text begins with a short correction cue (intervention signal)."""
    stripped = text.lstrip()
    if not stripped or stripped.startswith("```"):
        return False
    return _INTERVENTION_CORRECTION_RE.match(stripped[:80]) is not None


def _run_new_alert(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.cli.interactive_shell.execution_policy import (
        evaluate_investigation_launch,
        execution_allowed,
    )
    from app.cli.interactive_shell.tasks import TaskKind
    from app.cli.investigation import run_investigation_for_session

    policy = evaluate_investigation_launch(action_type="investigation")
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary="run RCA investigation from pasted alert text",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    ):
        session.record("alert", text, ok=False)
        return

    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        session.record_intervention("ctrl_c")
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", text, ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.new_alert")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)


def _dispatch_one_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
) -> None:
    """Route + dispatch one accepted line. Pure synchronous body.

    Used both from :class:`PersistentRepl` (wrapped in ``asyncio.to_thread``)
    and from the ``initial_input`` pre-seeding path. ``on_exit`` is called
    when a slash command requests REPL exit (e.g. ``/exit``); the caller
    decides what that means (in the persistent path, ``app.exit()``; in the
    pre-seeded path, an early return).
    """
    decision = route_input(text, session)
    kind = decision.route_kind.value
    session.last_route_decision = decision
    get_analytics().capture(
        Event.INTERACTIVE_SHELL_ROUTE_DECISION,
        decision.to_event_payload(),
    )
    if kind in ("follow_up", "new_alert") and _looks_like_correction(text):
        session.record_intervention("correction")

    if kind == "slash":
        cmd_text = text if text.startswith("/") else f"/{text}"
        try:
            should_continue = dispatch_slash(cmd_text, session, console)
        except Exception as exc:
            report_exception(exc, context="interactive_shell.slash_dispatch")
            console.print(
                f"[{ERROR}]command error:[/] {escape(str(exc))}"
                f" [{DIM}](the REPL is still running)[/]"
            )
            should_continue = True
        if not should_continue:
            on_exit()
        return

    if kind == "cli_help":
        answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return

    if kind == "cli_agent":
        turn = execute_cli_actions_with_metrics(text, session, console)
        fallback_to_llm = not turn.handled
        snapshot = session.record_terminal_turn(
            executed_count=turn.executed_count,
            executed_success_count=turn.executed_success_count,
            fallback_to_llm=fallback_to_llm,
        )
        capture_terminal_turn_summarized(
            planned_count=turn.planned_count,
            executed_count=turn.executed_count,
            executed_success_count=turn.executed_success_count,
            fallback_to_llm=fallback_to_llm,
            session_turn_index=snapshot.turn_index,
            session_fallback_count=snapshot.fallback_count,
            session_action_success_percent=snapshot.action_success_percent,
            session_fallback_rate_percent=snapshot.fallback_rate_percent,
        )
        if turn.handled:
            return
        answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        return

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        return

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console)
    session.record("follow_up", text)


def _run_initial_input(initial_input: str, session: ReplSession) -> int:
    """Test-harness path — drain pre-seeded input through the same dispatch logic."""
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    exit_requested = [False]

    def _early_exit() -> None:
        exit_requested[0] = True

    for line in initial_input.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        _dispatch_one_turn(stripped, session, console, on_exit=_early_exit)
        if exit_requested[0]:
            return 0
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

    # Prune dead-PID agent records and stale lockfiles before the REPL
    # starts. Errors are caught inside; a sweep failure must never prevent
    # the REPL from starting.
    run_startup_sweep()
    session = ReplSession()

    if initial_input:
        try:
            return _run_initial_input(initial_input, session)
        except (EOFError, KeyboardInterrupt):
            return 0

    # Interactive REPL — textual owns its own event loop. Calling ``app.run()``
    # synchronously avoids the ``asyncio.run`` wrapper that was causing
    # silent-render failures in some terminals (notably macOS Terminal.app).
    from app.cli.interactive_shell.textual_repl import (
        OpenSREApp,
        TextualConsole,
    )

    def _dispatch(text: str, app: OpenSREApp) -> None:
        console = TextualConsole(app)
        _dispatch_one_turn(text, session, console, on_exit=app.exit)

    app = OpenSREApp(session, _dispatch)
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        app.run()
    return 0


__all__ = ["run_repl"]
