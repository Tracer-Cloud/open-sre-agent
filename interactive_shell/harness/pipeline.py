"""Agentic pipeline for interactive-shell turns.

Every turn flows through :func:`handle_message_with_agent`, which delegates
terminal-action planning to the agent and falls through to the conversational
assistant when no terminal action handles the turn.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from config.llm_reasoning_effort import apply_reasoning_effort
from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from platform.analytics.cli import capture_terminal_turn_summarized


def handle_message_with_agent(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    execute_actions: Callable[..., TerminalActionExecutionResult],
    answer_agent: Callable[..., LlmRunInfo | None],
) -> None:
    """Handle one interactive-shell turn end to end."""
    # Clear any observation left by a prior turn so we only summarize discovery
    # output produced by *this* planner turn.
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
            if recorder is not None:
                recorder.set_response(assistant_text, run)
                recorder.flush()
            session.record("cli_agent", text)
            session.last_assistant_intent = "cli_agent_summarized"
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
    if recorder is not None:
        recorder.set_response(assistant_text, run)
        recorder.flush()
    session.record("cli_agent", text)
    session.last_assistant_intent = "cli_agent_handoff" if turn.handled else "cli_agent_fallback"


__all__ = ["handle_message_with_agent"]
