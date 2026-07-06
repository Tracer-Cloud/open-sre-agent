"""Shell adapter for one conversational answer turn.

Binds the interactive shell's Rich output, grounding caches, reasoning client,
and telemetry around core ``stream_answer``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness.ports import OutputSink
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
)
from core.agent_harness.session import Session
from core.agent_harness.turns.orchestrator import (
    stream_answer as core_stream_answer,
)
from core.agent_harness.turns.turn_plan import TurnPlan
from surfaces.interactive_shell.grounding.cli_reference import shell_prompt_context_provider
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo


def answer_shell_question(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    turn_plan: TurnPlan | None = None,
    output: OutputSink | None = None,
) -> LlmRunInfo | None:
    """Answer one shell question through the grounded conversational assistant."""
    resolved_output = resolve_output_sink(console, output)
    return core_stream_answer(
        message,
        session,
        resolved_output,
        prompts=shell_prompt_context_provider(session),
        reasoning=DefaultReasoningClientProvider(
            output=resolved_output,
            error_reporter=DefaultErrorReporter(),
            session=session,
        ),
        run_factory=DefaultRunRecordFactory(session),
        error_reporter=DefaultErrorReporter(),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_plan=turn_plan,
    )


__all__ = ["answer_shell_question"]
