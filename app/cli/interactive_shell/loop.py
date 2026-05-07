"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markup import escape

from app.analytics.cli import capture_terminal_turn_summarized
from app.cli.interactive_shell.agent_actions import execute_cli_actions_with_metrics
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up

# ``prompt_surface`` was extracted from this module; several names below are re-exported
# so existing callers — notably the test suite, which imports them as ``loop.NAME`` —
# keep working. The trailing F401 suppressions cover names not consumed inside this
# file but still required as module attributes for those external references.
from app.cli.interactive_shell.prompt_surface import (
    _PROMPT_RULE_CHAR,  # noqa: F401
    _SHIFT_ENTER_SEQUENCE,  # noqa: F401
    ReplInputLexer,  # noqa: F401
    ShellCompleter,  # noqa: F401
    _build_prompt_key_bindings,  # noqa: F401
    _build_prompt_session,
    _build_prompt_style,  # noqa: F401
    _prompt_message,
    _tab_expand_or_menu,  # noqa: F401
    render_submitted_prompt,
)
from app.cli.interactive_shell.router import classify_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ERROR
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception
from app.cli.support.prompt_support import repl_prompt_note_ctrl_c, repl_reset_ctrl_c_gate


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
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", text, ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.new_alert")
        # Exception repr may contain brackets (stack frame refs, config
        # dicts) that Rich would eat as markup tags — escape before printing.
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
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
    kind = classify_input(text, session)
    if kind == "slash":
        # Rewrite bare-word commands to their slash form before dispatch.
        cmd_text = text if text.startswith("/") else f"/{text}"
        try:
            should_continue = dispatch_slash(cmd_text, session, console)
        except Exception as exc:
            report_exception(exc, context="interactive_shell.slash_dispatch")
            console.print(
                f"[{TERMINAL_ERROR}]command error:[/] {escape(str(exc))}"
                " [dim](the REPL is still running)[/dim]"
            )
            should_continue = True
        return should_continue

    if kind == "cli_help":
        answer_cli_help(text, session, console, active_prompt=text)
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
        answer_cli_agent(text, session, console, active_prompt=text)
        session.record("cli_agent", text)
        return True

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        return True

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console, active_prompt=text)
    session.record("follow_up", text)
    return True


async def _repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    # force_terminal + truecolor so Rich always emits full ANSI, even after
    # prompt_toolkit has claimed and released stdout for input handling.
    # Without this, slash-command output after the first prompt renders as
    # literal escape codes in some terminal emulators.
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    session = ReplSession()
    prompt = _build_prompt_session()

    # Allow a single pre-seeded input for test harnesses
    if initial_input:
        for line in initial_input.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            render_submitted_prompt(console, session, stripped)
            kind = classify_input(stripped, session)
            if kind == "slash":
                cmd_text = stripped if stripped.startswith("/") else f"/{stripped}"
                if not dispatch_slash(cmd_text, session, console):
                    return 0
                console.print()
            elif kind == "cli_help":
                answer_cli_help(stripped, session, console, active_prompt=stripped)
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
                    answer_cli_agent(stripped, session, console, active_prompt=stripped)
                    session.record("cli_agent", stripped)
            elif kind == "new_alert":
                _run_new_alert(stripped, session, console)
            else:
                answer_follow_up(stripped, session, console, active_prompt=stripped)
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
