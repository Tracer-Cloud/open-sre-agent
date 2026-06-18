"""Execution bridge used by interactive shell dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from rich.console import Console

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as _agent_actions
from app.analytics.events import Event
from app.analytics.provider import JsonValue, get_analytics
from app.cli.interactive_shell import commands as _commands
from app.cli.interactive_shell.chat import cli_agent as _cli_agent
from app.cli.interactive_shell.prompt_logging import PromptRecorder
from app.cli.interactive_shell.routing.handle_message_with_agent.pipeline import (
    handle_message_with_agent,
)
from app.cli.interactive_shell.routing.types import RouteDecision
from app.cli.interactive_shell.runtime.session import ReplSession

answer_cli_agent = _cli_agent.answer_cli_agent
execute_cli_actions_with_metrics = _agent_actions.execute_cli_actions_with_metrics
dispatch_slash = _commands.dispatch_slash


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
        execute_actions=execute_cli_actions_with_metrics,
        answer_agent=answer_cli_agent,
        dispatch_command=dispatch_slash,
    )


__all__ = ["execute_routed_turn"]
