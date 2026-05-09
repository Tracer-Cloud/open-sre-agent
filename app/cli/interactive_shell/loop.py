"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable

from prompt_toolkit import PromptSession
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
from app.cli.interactive_shell.prompt_surface import (
    _build_prompt_session,
    _prompt_message,
    render_submitted_prompt,
)
from app.cli.interactive_shell.router import route_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import DIM, ERROR, WARNING
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception
from app.cli.support.prompt_support import repl_prompt_note_ctrl_c, repl_reset_ctrl_c_gate
from app.llm_reasoning_effort import apply_reasoning_effort

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
        with apply_reasoning_effort(session.reasoning_effort):
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
        # Exception repr may contain brackets (stack frame refs, config
        # dicts) that Rich would eat as markup tags — escape before printing.
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)


async def _run_one_turn(
    prompt: PromptSession[str],
    session: ReplSession,
    console: Console,
) -> bool:
    """Read one line of input and dispatch. Returns False to exit."""
    while True:
        try:
            text = await prompt.prompt_async(lambda: _prompt_message(session))
        except EOFError:
            console.print()
            return False
        except KeyboardInterrupt:
            if repl_prompt_note_ctrl_c(console):
                return False
            continue

        repl_reset_ctrl_c_gate()
        break

    text = text.strip()
    if not text:
        return True

    render_submitted_prompt(console, session, text)

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
        # Rewrite bare-word commands to their slash form before dispatch.
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
        return should_continue

    if kind == "cli_help":
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return True

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
            return True
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        return True

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        return True

    # follow_up — grounded answer against session.last_state
    with apply_reasoning_effort(session.reasoning_effort):
        answer_follow_up(text, session, console)
    session.record("follow_up", text)
    return True


async def _repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    # force_terminal + truecolor so Rich always emits full ANSI, even after
    # prompt_toolkit has claimed and released stdout for input handling.
    # Without this, slash-command output after the first prompt renders as
    # literal escape codes in some terminal emulators.
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    # Prune dead-PID agent records and stale lockfiles before the user's
    # first ``/agents`` call. Errors are caught inside; a sweep failure
    # must never prevent the REPL from starting.
    run_startup_sweep()
    session = ReplSession()
    prompt = _build_prompt_session()
    session.prompt_history_backend = prompt.history

    # Allow a single pre-seeded input for test harnesses
    if initial_input:
        for line in initial_input.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            render_submitted_prompt(console, session, stripped)

            decision = route_input(stripped, session)
            kind = decision.route_kind.value
            session.last_route_decision = decision
            get_analytics().capture(
                Event.INTERACTIVE_SHELL_ROUTE_DECISION,
                decision.to_event_payload(),
            )
            if kind == "slash":
                cmd_text = stripped if stripped.startswith("/") else f"/{stripped}"
                if not dispatch_slash(cmd_text, session, console):
                    return 0
                console.print()
            elif kind == "cli_help":
                with apply_reasoning_effort(session.reasoning_effort):
                    answer_cli_help(stripped, session, console)
                session.record("cli_help", stripped)
            elif kind == "cli_agent":
                turn = execute_cli_actions_with_metrics(stripped, session, console)
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
                if not turn.handled:
                    with apply_reasoning_effort(session.reasoning_effort):
                        answer_cli_agent(stripped, session, console)
                    session.record("cli_agent", stripped)
            elif kind == "new_alert":
                _run_new_alert(stripped, session, console)
            else:
                with apply_reasoning_effort(session.reasoning_effort):
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
        return asyncio.run(_repl_main(initial_input=initial_input, _config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl"]
