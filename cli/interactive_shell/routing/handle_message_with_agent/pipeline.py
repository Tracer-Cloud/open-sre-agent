"""Agentic pipeline for interactive-shell turns.

Every turn flows through :func:`handle_message_with_agent`. A deterministic
pre-LLM fast path dispatches literal slash commands, bare aliases, and
``opensre investigate`` quick-starts without calling the LLM; everything else
falls through to terminal-action planning and the conversational assistant.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.error_handling.exception_reporting import report_exception
from cli.interactive_shell.prompt_logging import LlmRunInfo, PromptRecorder
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from cli.interactive_shell.runtime.session import ReplSession
from cli.interactive_shell.ui import DIM, ERROR
from config.llm_reasoning_effort import apply_reasoning_effort
from platform.analytics.cli import capture_terminal_turn_summarized


def _build_empty_response_fallback(text: str, session: ReplSession) -> str:
    """Deterministic reply when the CLI-agent LLM returns an empty response."""
    condensed = " ".join(text.strip().split())
    if len(condensed) > 240:
        condensed = f"{condensed[:237]}..."

    if session.configured_integrations_known and not session.configured_integrations:
        guidance = (
            "No integrations are configured in this session yet. "
            "Use `/integrations` to set one up, or run `opensre investigate --help` "
            "to review investigation commands."
        )
    else:
        guidance = "You can run `opensre investigate --help` to review investigation commands."

    return f"I can help investigate this request: {condensed}\n\n{guidance}"


def _dispatch_command_turn(
    command_text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    on_exit: Callable[[], None],
    dispatch_command: Callable[..., bool],
) -> None:
    """Dispatch a deterministic slash/alias command without invoking the LLM."""
    try:
        should_continue = dispatch_command(
            command_text,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.slash_dispatch")
        console.print(
            f"[{ERROR}]command error:[/] {escape(str(exc))} [{DIM}](the REPL is still running)[/]"
        )
        should_continue = True
    session.last_assistant_intent = "slash"
    if not should_continue:
        on_exit()


def _summarize_observation_turn(
    text: str,
    session: ReplSession,
    console: Console,
    observation: str,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    answer_agent: Callable[..., LlmRunInfo | None],
) -> None:
    """Summarize a read-only discovery command's output into a direct answer.

    The command already streamed its raw output (e.g. the integrations table);
    this makes a follow-up assistant pass that reads that output and answers the
    user's original question concisely.
    """
    with apply_reasoning_effort(session.reasoning_effort):
        run = answer_agent(
            text,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            tool_observation=observation,
        )
    assistant_text = run.response_text if run is not None and run.response_text else ""
    if not assistant_text.strip():
        # The raw command output is already on screen; only add a fallback line
        # when the model gave us nothing to show.
        assistant_text = _build_empty_response_fallback(text, session)
        console.print(assistant_text, markup=False)
    if recorder is not None:
        recorder.set_response(assistant_text, run)
        recorder.flush()
    session.record("cli_agent", text)
    session.last_assistant_intent = "cli_agent_summarized"


def handle_message_with_agent(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    on_exit: Callable[[], None],
    execute_actions: Callable[..., TerminalActionExecutionResult],
    answer_agent: Callable[..., LlmRunInfo | None],
    dispatch_command: Callable[..., bool],
) -> None:
    """Handle one interactive-shell turn end to end.

    Pipeline:
    0. Deterministic fast path: dispatch literal slash commands, bare aliases,
       and ``opensre investigate`` quick-starts directly, skipping the LLM.
    1. Try explicit terminal actions.
    2. Record terminal-action metrics.
    3. Stop if actions handled the turn.
    4. Otherwise generate a conversational assistant answer.
    5. Persist the visible response.
    """
    command_text = deterministic_command_text(text)
    if command_text is not None:
        _dispatch_command_turn(
            command_text,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            on_exit=on_exit,
            dispatch_command=dispatch_command,
        )
        return

    # Clear any observation left by a prior turn so we only summarize discovery
    # output produced by *this* planner turn (the deterministic fast path above
    # returns before reaching here, so literal ``/integrations`` is never summarized).
    session.last_command_observation = None

    turn = execute_actions(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )
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
    observation = session.last_command_observation
    if turn.handled and (turn.has_unhandled_clause or turn.executed_count > 0):
        if observation and not turn.has_unhandled_clause and turn.executed_success_count > 0:
            # The planner ran a read-only discovery command to answer a question
            # (e.g. "is sentry installed?"). Feed its output back to the assistant
            # so the user gets a direct answer instead of only a raw table.
            _summarize_observation_turn(
                text,
                session,
                console,
                observation,
                recorder=recorder,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                answer_agent=answer_agent,
            )
            return
        # Denied or at least one real action executed; no LLM reply needed.
        session.last_assistant_intent = (
            "cli_agent_denied" if turn.has_unhandled_clause else "cli_agent_handled"
        )
        if recorder is not None:
            recorder.set_response(turn.response_text)
            recorder.flush()
        return

    with apply_reasoning_effort(session.reasoning_effort):
        run = answer_agent(text, session, console, confirm_fn=confirm_fn, is_tty=is_tty)
    assistant_text = run.response_text if run is not None and run.response_text else ""
    if not assistant_text.strip():
        assistant_text = _build_empty_response_fallback(text, session)
        console.print(assistant_text, markup=False)
    if recorder is not None:
        recorder.set_response(assistant_text, run)
        recorder.flush()
    session.record("cli_agent", text)
    session.last_assistant_intent = "cli_agent_handoff" if turn.handled else "cli_agent_fallback"


__all__ = ["handle_message_with_agent"]
