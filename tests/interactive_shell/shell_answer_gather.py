"""Answer with the shell's HeadlessAgent build, without a full turn.

The REPL does not add a stage. This helper wires the same session, output,
console, and error reporter the shell uses, then calls the harness answer
function. Tests that exercise that path in isolation compose that here.
"""

from __future__ import annotations

from rich.console import Console

from core.agent_harness.ports import AnswerRequest
from core.agent_harness.runtime import DefaultHeadlessBuild
from core.agent_harness.turns.orchestrator import stream_answer
from surfaces.interactive_shell.grounding.cli_reference import shell_prompt_context_provider
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellErrorReporter,
    ShellOutputSink,
)
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


__all__ = ["stream_shell_answer"]
