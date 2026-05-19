"""Interactive shell loop package."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.orchestration.agent_actions as _agent_actions
from app.analytics.cli import capture_terminal_turn_summarized
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell import commands as _commands
from app.cli.interactive_shell.chat import cli_agent as _cli_agent
from app.cli.interactive_shell.chat import cli_help as _cli_help
from app.cli.interactive_shell.prompting import follow_up as _follow_up
from app.cli.interactive_shell.routing import router as _router
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import ANSI_DIM, ANSI_RESET, DIM, ERROR
from app.cli.interactive_shell.ui.choice_menu import repl_tty_interactive
from app.cli.interactive_shell.ui.streaming import _CHARS_PER_TOKEN
from app.cli.support.exception_reporting import report_exception
from app.llm_reasoning_effort import apply_reasoning_effort

from .dispatch import (
    DispatchCancelled,
    _build_cancel_key_bindings,
    _install_session_key_bindings,
    _looks_like_cancel_request,
    _looks_like_confirmation_answer,
    _looks_like_correction,
    _route_confirm_through_prompt,
    _run_initial_input,
)
from .dispatch import (
    _dispatch_should_show_spinner as _dispatch_should_show_spinner_impl,
)
from .entrypoint import _repl_main, run_repl
from .execution import run_new_alert as _run_new_alert
from .state import ReplState as _ReplState
from .state import SpinnerState
from .ui_runtime import _run_interactive, _StreamingConsole

route_input = _router.route_input
resolve_cli_command = _router.resolve_cli_command
answer_cli_help = _cli_help.answer_cli_help
answer_cli_agent = _cli_agent.answer_cli_agent
answer_follow_up = _follow_up.answer_follow_up
execute_cli_actions_with_metrics = _agent_actions.execute_cli_actions_with_metrics
dispatch_slash = _commands.dispatch_slash


class _SpinnerState(SpinnerState):
    """Compatibility wrapper so tests can monkeypatch ``loop.get_app_or_none``."""

    def toolbar_ansi(self) -> ANSI:
        if self.streaming:
            hint = "esc to interrupt"
        else:
            hint = "/ for commands  ·  ↑↓ history"
            app = get_app_or_none()
            if app is not None and app.current_buffer.text:
                hint += "  ·  esc to clear"
        return ANSI(f"{ANSI_DIM}{hint}{ANSI_RESET}")


def _dispatch_should_show_spinner(text: str, session: ReplSession) -> bool:
    return _dispatch_should_show_spinner_impl(text, session)


def _dispatch_needs_exclusive_stdin(text: str, session: ReplSession) -> bool:
    if not repl_tty_interactive():
        return False
    t = text.strip()
    if not t:
        return False
    command_decision = resolve_cli_command(t, session)
    if command_decision is None:
        return False
    dispatch_text = command_decision.command_text or t
    parts = dispatch_text.split()
    if not parts:
        return False
    name = parts[0].lower()
    args = [arg.lower() for arg in parts[1:]]
    if name in {"/exit", "/quit"}:
        return True
    if (
        name
        in {
            "/history",
            "/help",
            "/integrations",
            "/list",
            "/mcp",
            "/model",
            "/template",
            "/trust",
            "/verbose",
            "/?",
        }
        and not args
    ):
        return True
    if name == "/tests" and not args:
        return True
    return bool(args and (name, args[0]) in {("/integrations", "setup"), ("/mcp", "connect")})


def _dispatch_one_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
) -> None:
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
        cmd_text = decision.command_text
        if not cmd_text:
            command_decision = resolve_cli_command(text.strip(), session)
            cmd_text = command_decision.command_text if command_decision else text.strip()
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
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return

    if kind == "cli_agent":
        turn = execute_cli_actions_with_metrics(text, session, console, confirm_fn=confirm_fn)
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
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_agent(text, session, console, confirm_fn=confirm_fn)
        session.record("cli_agent", text)
        return

    if kind == "new_alert":
        _run_new_alert(text, session, console, confirm_fn=confirm_fn)
        return

    with apply_reasoning_effort(session.reasoning_effort):
        answer_follow_up(text, session, console)
    session.record("follow_up", text)


__all__ = [
    "DispatchCancelled",
    "_CHARS_PER_TOKEN",
    "_ReplState",
    "_SpinnerState",
    "_StreamingConsole",
    "_build_cancel_key_bindings",
    "_dispatch_needs_exclusive_stdin",
    "_dispatch_one_turn",
    "_dispatch_should_show_spinner",
    "_install_session_key_bindings",
    "_looks_like_cancel_request",
    "_looks_like_confirmation_answer",
    "_looks_like_correction",
    "_run_new_alert",
    "_repl_main",
    "_route_confirm_through_prompt",
    "_run_initial_input",
    "_run_interactive",
    "dispatch_slash",
    "get_app_or_none",
    "repl_tty_interactive",
    "run_repl",
]
