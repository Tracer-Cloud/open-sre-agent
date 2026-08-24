"""Answer and gather with the shell's HeadlessAgent build, without a full turn.

The REPL does not add a stage. These helpers wire the same session, output,
console, and error reporter the shell uses, then call the harness answer/gather
functions. Tests that exercise those paths in isolation compose that here.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from core.agent_harness.ports import AnswerRequest
from core.agent_harness.runtime import DefaultHeadlessBuild
from core.agent_harness.turns.evidence_driver import GatherAgentFactory, gather_tool_evidence
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.orchestrator import stream_answer
from surfaces.interactive_shell.grounding.cli_reference import shell_prompt_context_provider
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellErrorReporter,
    ShellOutputSink,
)
from surfaces.interactive_shell.runtime.integration_tool_gathering import shell_gather_phase
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import LlmRunInfo


def stream_shell_answer(
    message: str,
    session: Session,
    console: Console,
    *,
    request: AnswerRequest | None = None,
) -> LlmRunInfo | None:
    """The agent's answer stage with the shell's DefaultHeadlessBuild."""
    build = DefaultHeadlessBuild(
        session=session,  # type: ignore[arg-type]
        output=ShellOutputSink(console),
        console=console,
        error_reporter=ShellErrorReporter(),
    )
    return stream_answer(
        message,
        session,
        build.output,
        prompts=shell_prompt_context_provider(session),
        reasoning=build.reasoning(),
        run_factory=build.run_factory(),
        error_reporter=build._error_reporter,  # noqa: SLF001
        request=request if request is not None else AnswerRequest(),
    )


def gather_shell_evidence(
    message: str,
    session: Session,
    console: Console,
    *,
    agent_factory: GatherAgentFactory | None = None,
    resolved_integrations: dict[str, Any] | None = None,
) -> str | GatheredEvidence | None:
    """The agent's gather stage with the shell's GatherPhase."""
    gather = shell_gather_phase(session, console)
    return gather_tool_evidence(
        message,
        session,
        on_progress=gather.on_progress,
        persist=gather.persist,
        error_reporter=ShellErrorReporter(),
        agent_factory=agent_factory,
        resolved_integrations=resolved_integrations,
    )


__all__ = ["gather_shell_evidence", "stream_shell_answer"]
