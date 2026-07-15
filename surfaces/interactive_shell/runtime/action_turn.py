"""Shell adapter for one action-selection turn.

Binds the interactive shell's console, session, and default providers around
core ``run_action_agent_turn``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.llm_resolution import default_llm_factory
from core.agent_harness.ports import OutputSink
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ToolCallingDeps, run_action_agent_turn
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import resolve_literal_slash_typo
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.investigation_adapter import (
    repl_investigation_launch_ports,
)
from surfaces.interactive_shell.runtime.llm_provider_adapter import (
    repl_llm_provider_ports,
)
from surfaces.interactive_shell.runtime.slash_adapter import (
    repl_slash_ports,
)
from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
    ReplSubprocessPresenter,
)
from surfaces.interactive_shell.runtime.task_cancel_adapter import (
    repl_task_cancel_ports,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver


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


def _subprocess_presenter_factory(
    session: Session,
    console: Console,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    action_already_listed: bool,
) -> ReplSubprocessPresenter:
    return ReplSubprocessPresenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def run_action_tool_turn(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    deps: ToolCallingDeps | None = None,
    turn_plan: TurnPlan | None = None,
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
            observer_factory=lambda msg: ActionRenderObserver(
                session=session, console=console, message=msg
            ),
            subprocess_presenter_factory=_subprocess_presenter_factory,
            investigation_ports_factory=repl_investigation_launch_ports,
            llm_provider_ports_factory=repl_llm_provider_ports,
            task_cancel_ports_factory=repl_task_cancel_ports,
            slash_ports_factory=repl_slash_ports,
        ),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        deps=effective_deps,
        turn_plan=turn_plan,
        error_reporter=DefaultErrorReporter(),
        tool_hooks=tool_hooks,
    )


__all__ = ["run_action_tool_turn"]
