"""Shell adapter for one action-selection turn.

Binds the interactive shell's console, session, and default providers around
core ``run_action_agent_turn``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness.agents.action_agent import ToolCallingDeps, run_action_agent_turn
from core.agent_harness.factories import default_llm_factory
from core.agent_harness.models.turn_results import ToolCallingTurnResult
from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.ports import OutputSink
from core.agent_harness.providers.default_providers import DefaultErrorReporter, DefaultToolProvider
from core.agent_harness.session import Session
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import resolve_literal_slash_typo
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver


def _action_observer_factory(
    session: Session,
    console: Console,
    message: str,
) -> ActionRenderObserver:
    return ActionRenderObserver(session=session, console=console, message=message)


def _complete_literal_slash_typo_turn(
    message: str,
    session: Session,
    output: OutputSink,
) -> ToolCallingTurnResult | None:
    """Handle unknown slash roots and invalid subcommands before tool validation."""
    typo = resolve_literal_slash_typo(message, SLASH_COMMANDS)
    if typo is None:
        return None
    output.print()
    output.print(typo.message)
    session.record(
        "slash",
        message.strip(),
        ok=False,
        response_text=typo.message,
        slash_outcome=typo.outcome,
    )
    return ToolCallingTurnResult(0, 1, 0, False, True, response_text=typo.message)


def run_action_tool_turn(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    deps: ToolCallingDeps | None = None,
    turn_snapshot: TurnSnapshot | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """Run one action-selection turn through core with shell adapters bound."""
    resolved_output = resolve_output_sink(console, output)
    typo_result = _complete_literal_slash_typo_turn(message, session, resolved_output)
    if typo_result is not None:
        return typo_result
    effective_deps = (
        deps
        if deps is not None and deps.llm_factory is not None
        else ToolCallingDeps(llm_factory=default_llm_factory)
    )
    return run_action_agent_turn(
        message,
        session,
        output=resolved_output,
        tools=DefaultToolProvider(
            session,
            console,
            request_exit=request_exit,
            observer_factory=_action_observer_factory,
        ),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        deps=effective_deps,
        turn_snapshot=turn_snapshot,
        error_reporter=DefaultErrorReporter(),
        tool_hooks=tool_hooks,
    )


__all__ = ["run_action_tool_turn"]
