"""
Execute submitted interactive-shell turns through the shared agent harness.

Comment Vincent: Again 5 functions that are largely overlapping in functionality.
Comment Vincent: This file is trying to mimic the functions in the core agent_harness package. It shouldn't do that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.agents.action_agent import ToolCallingDeps, run_agent_turn
from core.agent_harness.agents.turn_orchestrator import (
    answer_cli_agent as run_core_answer_cli_agent,
)
from core.agent_harness.agents.turn_orchestrator import run_turn
from core.agent_harness.models.turn_context import TurnContext
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.ports import OutputSink
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
    DefaultToolProvider,
)
from core.agent_harness.session import ReplSession
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import resolve_literal_slash_typo
from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder

# Dependency seams used by the harness turn-routing tests.
RunActionToolTurn = Callable[..., ToolCallingTurnResult]
GatherEvidence = Callable[..., "str | None"]
AnswerShellQuestion = Callable[..., "LlmRunInfo | None"]


def _default_llm_factory() -> Any:
    from core.llm import agent_llm_client

    return agent_llm_client.get_agent_llm()


def _resolve_output_sink(console: Console, output: OutputSink | None) -> OutputSink:
    if output is not None:
        return output
    return ShellOutputSink(console)


def _action_observer_factory(
    session: ReplSession,
    console: Console,
    message: str,
) -> ActionRenderObserver:
    return ActionRenderObserver(session=session, console=console, message=message)


def _complete_literal_slash_typo_turn(
    message: str,
    session: ReplSession,
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
    return ToolCallingTurnResult(
        0,
        1,
        0,
        False,
        True,
        response_text=typo.message,
    )


def run_action_tool_turn(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    deps: ToolCallingDeps | None = None,
    turn_ctx: TurnContext | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """Run one action-selection turn through core with shell adapters bound."""
    resolved_output = _resolve_output_sink(console, output)
    typo_result = _complete_literal_slash_typo_turn(message, session, resolved_output)
    if typo_result is not None:
        return typo_result
    effective_deps = (
        deps
        if deps is not None and deps.llm_factory is not None
        else ToolCallingDeps(llm_factory=_default_llm_factory)
    )
    return run_agent_turn(
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
        turn_ctx=turn_ctx,
        error_reporter=DefaultErrorReporter(),
        tool_hooks=tool_hooks,
    )


def answer_shell_question(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    turn_ctx: TurnContext | None = None,
    output: OutputSink | None = None,
) -> LlmRunInfo | None:
    """Answer one shell question through the grounded conversational assistant.

    Delegates to :func:`core.agent_harness.agents.turn_orchestrator.answer_cli_agent`, supplying the shell
    adapters for Rich output, grounding caches, reasoning client, and telemetry.
    """
    return run_core_answer_cli_agent(
        message,
        session,
        _resolve_output_sink(console, output),
        prompts=DefaultPromptContextProvider(session),
        reasoning=DefaultReasoningClientProvider(
            output=_resolve_output_sink(console, output),
            error_reporter=DefaultErrorReporter(),
        ),
        run_factory=DefaultRunRecordFactory(session),
        error_reporter=DefaultErrorReporter(),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_ctx=turn_ctx,
    )


def execute_shell_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    execute_actions: RunActionToolTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerShellQuestion | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ShellTurnResult:
    """Execute one submitted interactive-shell turn.

    The action driver, gather pass, and conversational assistant are bound to the
    live ``session``/``console`` here (so injected test doubles keep their
    ``(text, session, console, ...)`` shape) and handed to
    :func:`core.agent_harness.agents.turn_orchestrator.run_turn`, which performs the pure path routing.
    """
    from core.agent_harness.session.compaction import auto_compact_if_needed

    # This is problematic bwecause at a random place you have session compaction. This should happen in the agent layer in core, and not in the interactive shell in a random function!!!!
    auto_compact_if_needed(session)
    _execute = execute_actions or run_action_tool_turn
    _gather = gather_evidence or gather_integration_tool_evidence
    _answer = answer_agent or answer_shell_question
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)
    resolved_output = _resolve_output_sink(console, output)

    def execute_bound(
        t: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_ctx: TurnContext | None = None,
    ) -> ToolCallingTurnResult:
        return _execute(
            t,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=request_exit,
            turn_ctx=turn_ctx,
            output=resolved_output,
            tool_hooks=tool_hooks,
        )

    def answer_bound(t: str, **kwargs: Any) -> LlmRunInfo | None:
        # Pure passthrough so the engine controls the exact call shape: when it
        # omits ``tool_observation_on_screen`` (no evidence gathered) the bound
        # call omits it too, matching the plain conversational path.
        return _answer(
            t,
            session,
            console,
            output=resolved_output,
            **kwargs,
        )

    def gather_bound(t: str, *, is_tty: bool | None = None) -> str | None:
        return _gather(t, session, console, is_tty=is_tty)

    return run_turn(
        text,
        session,
        execute_actions=execute_bound,
        answer=answer_bound,
        gather=gather_bound,
        accounting=accounting,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


__all__ = [
    "AnswerShellQuestion",
    "GatherEvidence",
    "RunActionToolTurn",
    "answer_shell_question",
    "execute_shell_turn",
    "run_action_tool_turn",
]
