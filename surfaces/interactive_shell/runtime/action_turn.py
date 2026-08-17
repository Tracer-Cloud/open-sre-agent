"""Shell adapter for one action-selection turn.

Binds the interactive shell's console, session, and default providers around
core ``ActionTurnRunner``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness import OutputSink, ToolCallingTurnResult
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.llm_resolution import default_llm_factory
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import (
    ActionTurnRunner,
    ToolCallingDeps,
)
from core.agent_harness.turns.turn_plan import TurnPlan
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import resolve_literal_slash_typo
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellOutputSink,
    resolve_output_sink,
)
from surfaces.interactive_shell.runtime.background import runner as background_runner
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
from tools.interactive_shell.shared.investigation_launch import InvestigationLaunchPorts


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


def _investigation_ports_factory() -> InvestigationLaunchPorts:
    return repl_investigation_launch_ports(
        start_background_text=background_runner.start_background_text_investigation,
        start_background_sample=background_runner.start_background_template_investigation,
    )


class ShellActionRunner:
    """Runs action turns for one shell session.

    Session, tool wiring, error reporting, and the core
    :class:`~core.agent_harness.turns.action_driver.ActionTurnRunner` are built
    once. Per turn, :meth:`bind_turn` retargets the console/output (spinner
    streaming console) without rebuilding the runner; only ``confirm_fn``,
    ``is_tty``, and ``turn_plan`` are passed to :meth:`run`.
    """

    def __init__(
        self,
        session: Session,
        console: Console,
        *,
        request_exit: Callable[[], None] | None = None,
        deps: ToolCallingDeps | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        self._session = session
        self._console = console
        self._request_exit = request_exit
        self._deps = (
            deps
            if deps is not None and deps.llm_factory is not None
            else ToolCallingDeps(llm_factory=default_llm_factory)
        )
        self._external_output = output
        self._tool_hooks = tool_hooks
        self._sink = resolve_output_sink(console, output)
        self._tools = DefaultToolProvider(
            session,
            console,
            request_exit=request_exit,
            observer_factory=lambda msg: ActionRenderObserver(
                session=self._session, console=self._console, message=msg
            ),
            subprocess_presenter_factory=_subprocess_presenter_factory,
            investigation_ports_factory=_investigation_ports_factory,
            llm_provider_ports_factory=repl_llm_provider_ports,
            task_cancel_ports_factory=repl_task_cancel_ports,
            slash_ports_factory=repl_slash_ports,
        )
        self._error_reporter = DefaultErrorReporter()
        self._action_runner = self._new_action_runner()

    def _new_action_runner(self) -> ActionTurnRunner:
        return ActionTurnRunner(
            output=self._sink,
            tools=self._tools,
            deps=self._deps,
            error_reporter=self._error_reporter,
            tool_hooks=self._tool_hooks,
        )

    def bind_turn(
        self,
        *,
        console: Console | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        """Retarget turn-scoped ports without rebuilding the runner when possible."""
        runner_changed = False
        if console is not None and console is not self._console:
            self._console = console
            self._tools.bind_console(console)
            if self._external_output is None and isinstance(self._sink, ShellOutputSink):
                self._sink.bind_console(console)
        if output is not None and output is not self._external_output:
            self._external_output = output
            self._sink = resolve_output_sink(self._console, output)
            runner_changed = True
        if tool_hooks is not None and tool_hooks is not self._tool_hooks:
            self._tool_hooks = tool_hooks
            runner_changed = True
        if runner_changed:
            self._action_runner = self._new_action_runner()

    def run(
        self,
        message: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> ToolCallingTurnResult:
        """Run one action-selection turn through core with shell adapters bound."""
        typo_result = _complete_literal_slash_typo_turn(message, self._session, self._sink)
        if typo_result is not None:
            return typo_result
        return self._action_runner.run(
            message,
            self._session,
            turn_plan=turn_plan,
            is_tty=is_tty,
            confirm_fn=confirm_fn,
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
    """Run a single action turn without keeping a runner around.

    The one-shot form: everything a :class:`ShellActionRunner` would hold is
    given per call. A surface running many turns should build the runner once
    instead.
    """
    return ShellActionRunner(
        session=session,
        console=console,
        request_exit=request_exit,
        deps=deps,
        output=output,
        tool_hooks=tool_hooks,
    ).run(message, confirm_fn=confirm_fn, is_tty=is_tty, turn_plan=turn_plan)


__all__ = ["ShellActionRunner", "run_action_tool_turn"]
