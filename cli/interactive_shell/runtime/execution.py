"""Execution bridge used by interactive shell dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from rich.console import Console

import cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as _agent_actions
from cli.interactive_shell.chat import cli_agent as _cli_agent
from cli.interactive_shell.chat.tool_gathering import gather_tool_evidence
from cli.interactive_shell.command_registry import dispatch_slash
from cli.interactive_shell.prompt_logging import LlmRunInfo, PromptRecorder
from cli.interactive_shell.routing.handle_message_with_agent.pipeline import (
    handle_message_with_agent,
)
from cli.interactive_shell.routing.types import RouteDecision
from cli.interactive_shell.runtime.session import ReplSession
from platform.analytics.events import Event
from platform.analytics.provider import JsonValue, get_analytics
from platform.analytics.repl_context import bind_cli_session_id, reset_cli_session_id

answer_cli_agent = _cli_agent.answer_cli_agent
execute_cli_actions = _agent_actions.execute_cli_actions


def _answer_cli_agent_with_tools(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
) -> LlmRunInfo | None:
    """Answer a turn, first gathering live evidence from registered tools.

    On the main fallback path (no existing ``tool_observation``), this runs a
    bounded tool-calling loop over the same tools the investigation uses. If it
    collects any evidence, that evidence is handed to ``answer_cli_agent`` as an
    off-screen observation so the assistant answers from real integration data.
    The summarize path (which already carries a ``tool_observation``) is passed
    through unchanged.

    ``answer_cli_agent`` is read from the module namespace so existing test seams
    that patch ``runtime.execution.answer_cli_agent`` keep working.
    """
    if tool_observation is None:
        gathered = gather_tool_evidence(text, session, console, is_tty=is_tty)
        if gathered:
            return answer_cli_agent(
                text,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                tool_observation=gathered,
                tool_observation_on_screen=False,
            )
    return answer_cli_agent(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=tool_observation,
    )


def execute_routed_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    decision: RouteDecision,
) -> None:
    """Record route telemetry and hand the turn to the agent."""
    session_token = bind_cli_session_id(session.session_id)
    try:
        recorder = PromptRecorder.start(
            session=session, text=text, route_kind=decision.route_kind.value
        )
        session.last_route_decision = decision
        get_analytics().capture(
            Event.INTERACTIVE_SHELL_ROUTE_DECISION,
            cast(dict[str, JsonValue], decision.to_event_payload()),
        )

        handle_message_with_agent(
            text,
            session,
            console,
            recorder=recorder,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            on_exit=on_exit,
            execute_actions=execute_cli_actions,
            answer_agent=_answer_cli_agent_with_tools,
            dispatch_command=dispatch_slash,
        )
    finally:
        reset_cli_session_id(session_token)


__all__ = ["execute_routed_turn"]
