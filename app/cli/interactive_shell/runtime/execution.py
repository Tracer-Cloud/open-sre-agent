"""Execution bridges used by interactive shell dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as _agent_actions
from app.analytics.cli import capture_terminal_turn_summarized
from app.analytics.events import Event
from app.analytics.provider import JsonValue, get_analytics
from app.cli.interactive_shell import commands as _commands
from app.cli.interactive_shell.chat import cli_agent as _cli_agent
from app.cli.interactive_shell.chat import cli_help as _cli_help
from app.cli.interactive_shell.prompt_logging import PromptRecorder
from app.cli.interactive_shell.prompting import follow_up as _follow_up
from app.cli.interactive_shell.routing.types import RouteDecision
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, WARNING
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception
from app.llm_reasoning_effort import apply_reasoning_effort

answer_cli_help = _cli_help.answer_cli_help
answer_cli_agent = _cli_agent.answer_cli_agent
answer_follow_up = _follow_up.answer_follow_up
execute_cli_actions_with_metrics = _agent_actions.execute_cli_actions_with_metrics
dispatch_slash = _commands.dispatch_slash


def run_new_alert(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> str | None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.analytics.cli import track_investigation
    from app.analytics.source import EntrypointSource, TriggerMode
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
        evaluate_investigation_launch,
        execution_allowed,
    )
    from app.cli.interactive_shell.runtime.tasks import TaskKind
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
        return None

    task = session.task_registry.create(TaskKind.INVESTIGATION, command="free-text investigation")
    task.mark_running()
    try:
        with (
            track_investigation(
                entrypoint=EntrypointSource.CLI_PASTE,
                trigger_mode=TriggerMode.PASTE,
                interactive=True,
            ),
            apply_reasoning_effort(session.reasoning_effort),
        ):
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
        return None
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return None
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.new_alert")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return None

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)
    if root:
        return str(root)
    slack_message = final_state.get("slack_message")
    return str(slack_message) if slack_message else None


def execute_routed_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
    decision: RouteDecision,
) -> None:
    """Route + execute one accepted line."""
    kind = decision.route_kind.value
    recorder = PromptRecorder.start(session=session, text=text, route_kind=kind)
    session.last_route_decision = decision
    get_analytics().capture(
        Event.INTERACTIVE_SHELL_ROUTE_DECISION,
        cast("dict[str, JsonValue]", decision.to_event_payload()),
    )

    if kind == "slash":
        cmd_text = decision.command_text
        if not cmd_text:
            cmd_text = text.strip()
        try:
            should_continue = dispatch_slash(cmd_text, session, console)
        except Exception as exc:
            report_exception(exc, context="interactive_shell.slash_dispatch")
            console.print(
                f"[{ERROR}]command error:[/] {escape(str(exc))}"
                f" [{DIM}](the REPL is still running)[/]"
            )
            should_continue = True
        session.last_assistant_intent = "slash"
        if not should_continue:
            on_exit()
        return

    if kind == "cli_help":
        with apply_reasoning_effort(session.reasoning_effort):
            run = answer_cli_help(text, session, console)
        if recorder is not None:
            recorder.set_response(
                run.response_text if run is not None and run.response_text else "", run
            )
            recorder.flush()
        session.record("cli_help", text)
        session.last_assistant_intent = "cli_help"
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
        if turn.handled and (turn.has_unhandled_clause or turn.executed_count > 0):
            # Denied or at least one real action executed — done, no LLM reply needed.
            if turn.has_unhandled_clause:
                session.last_assistant_intent = "cli_agent_denied"
            else:
                session.last_assistant_intent = "cli_agent_handled"
            if recorder is not None:
                recorder.flush()
            return
        # Either the planner produced no actions (fallback) or a handoff-only plan
        # (executed_count == 0, handled == True). In both cases the assistant must
        # generate an actual reply.
        with apply_reasoning_effort(session.reasoning_effort):
            run = answer_cli_agent(text, session, console, confirm_fn=confirm_fn)
        if recorder is not None:
            assistant_text = run.response_text if run is not None and run.response_text else ""
            recorder.set_response(assistant_text, run)
            recorder.flush()
        session.record("cli_agent", text)
        session.last_assistant_intent = (
            "cli_agent_handoff" if turn.handled else "cli_agent_fallback"
        )
        return

    if kind == "new_alert":
        response = run_new_alert(text, session, console, confirm_fn=confirm_fn)
        if recorder is not None:
            recorder.set_response(response or "")
            recorder.flush()
        session.last_assistant_intent = "investigation"
        return

    with apply_reasoning_effort(session.reasoning_effort):
        run = answer_follow_up(text, session, console)
    if recorder is not None:
        recorder.set_response(
            run.response_text if run is not None and run.response_text else "", run
        )
        recorder.flush()
    session.record("follow_up", text)
    session.last_assistant_intent = "follow_up"


__all__ = ["execute_routed_turn", "run_new_alert"]
