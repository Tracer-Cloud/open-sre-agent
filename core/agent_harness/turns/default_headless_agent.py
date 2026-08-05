"""Build a :class:`HeadlessAgent` with the standard default port stack.

Gateway, scheduled digests, and other headless surfaces share this wiring so
tool/prompt/reasoning defaults stay aligned.
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.ports import OutputSink, PromptContextProvider, TurnAccounting
from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider
from core.agent_harness.tools.tool_provider import (
    ActionObserverFactory,
    DefaultToolProvider,
    SlashPortsFactory,
    SubprocessPresenterFactory,
)
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent

if TYPE_CHECKING:
    from core.agent_harness.session.session_core import SessionCore

LoggerLike = logging.Logger


def _default_prompts(session: SessionCore, surface: str | None) -> PromptContextProvider:
    """The built-in grounding context, when a caller supplies none."""
    if surface is not None:
        return DefaultPromptContextProvider(session, surface=surface)
    return DefaultPromptContextProvider(session)


def build_default_headless_agent(
    *,
    session: SessionCore,
    output: OutputSink,
    console: Any | None = None,
    logger: LoggerLike | None = None,
    message: str | None = None,
    accounting: TurnAccounting | None = None,
    surface: str | None = None,
    tool_action_logger: LoggerLike | None = None,
    observer_factory: ActionObserverFactory | None = None,
    subprocess_presenter_factory: SubprocessPresenterFactory | None = None,
    slash_ports_factory: SlashPortsFactory | None = None,
    prompts: PromptContextProvider | None = None,
    gather_enabled: bool = True,
    gather_max_iterations: int | None = None,
    is_tty: bool = False,
) -> HeadlessAgent:
    """Return a :class:`HeadlessAgent` wired with default harness ports.

    ``console`` and ``logger`` default to headless-safe instances (a
    swallow-everything :class:`rich.console.Console` and the ``"opensre"``
    logger); pass them only to redirect tool rendering or logging. Pass
    ``message`` (or an explicit ``accounting``) when the agent should
    account for a single turn at construction time. Gateway reuse binds
    accounting later via :meth:`HeadlessAgent.bind_turn`.
    """
    if console is None:
        console = Console(force_terminal=False, file=StringIO())
    if logger is None:
        logger = logging.getLogger("opensre")
    error_reporter = DefaultErrorReporter(logger)
    turn_accounting = accounting
    if turn_accounting is None and message is not None:
        turn_accounting = DefaultTurnAccounting(session, message)
    return HeadlessAgent(
        session=session,
        output=output,
        tools=DefaultToolProvider(
            session,
            console,
            tool_action_logger=tool_action_logger or logger,
            observer_factory=observer_factory,
            subprocess_presenter_factory=subprocess_presenter_factory,
            slash_ports_factory=slash_ports_factory,
        ),
        # ``is not None``, not ``or``: a provider defining __bool__ would be
        # silently replaced. Matches HeadlessAgent's own selection.
        prompts=prompts if prompts is not None else _default_prompts(session, surface),
        reasoning=DefaultReasoningClientProvider(
            output=output,
            error_reporter=error_reporter,
            session=session,
        ),
        run_factory=DefaultRunRecordFactory(session),
        accounting=turn_accounting,
        error_reporter=error_reporter,
        gather_enabled=gather_enabled,
        gather_max_iterations=gather_max_iterations,
        is_tty=is_tty,
    )


__all__ = ["build_default_headless_agent"]
