"""Interactive-shell entry to the action tool-calling turn.

The loop body itself is the decoupled :func:`core.agent.action_agent.run_agent_turn`; this
module is the thin terminal adapter that builds the shell ports (Rich console
output sink, registry-backed tool provider, error reporter) and delegates to it.
``_default_llm_factory`` is kept here as the patch point the harness tests use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent.action_agent import ToolCallingDeps, run_agent_turn
from core.agent.turn_context import TurnContext
from core.agent.turn_results import ToolCallingTurnResult
from interactive_shell.agent_shell.adapters import (
    ShellErrorReporter,
    ShellOutputSink,
    ShellToolProvider,
)
from interactive_shell.session import ReplSession


def _default_llm_factory() -> Any:
    from core.llm import agent_llm_client

    return agent_llm_client.get_agent_llm()


def run_tool_calling_turn(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ToolCallingDeps | None = None,
    turn_ctx: TurnContext | None = None,
) -> ToolCallingTurnResult:
    """Run one shell action tool-calling turn through the shared agent driver.

    ``turn_ctx`` is the immutable per-turn snapshot assembled at turn start; when
    present it is used to build the action-agent system prompt so the prompt
    reflects turn-start state rather than the live session.
    """
    effective_deps = (
        deps
        if deps is not None and deps.llm_factory is not None
        else ToolCallingDeps(llm_factory=_default_llm_factory)
    )
    return run_agent_turn(
        message,
        session,
        output=ShellOutputSink(console),
        tools=ShellToolProvider(session, console),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        deps=effective_deps,
        turn_ctx=turn_ctx,
        error_reporter=ShellErrorReporter(),
    )


__all__ = [
    "ToolCallingDeps",
    "run_tool_calling_turn",
]
