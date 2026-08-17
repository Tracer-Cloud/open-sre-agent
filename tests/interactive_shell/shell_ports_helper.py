"""Answer and gather through the shell's ports, without a full turn.

The REPL adds no stage of its own — answering and gathering are the agent's,
configured by the shell's ports. Tests that exercise those paths in isolation
compose the same ports here.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from core.agent_harness.ports import AnswerRequest
from core.agent_harness.runtime import DefaultPorts
from core.agent_harness.turns.evidence_driver import GatherAgentFactory, gather_tool_evidence
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.orchestrator import stream_answer
from surfaces.interactive_shell.grounding.cli_reference import shell_prompt_context_provider
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellErrorReporter,
    ShellOutputSink,
)
from surfaces.interactive_shell.runtime.integration_tool_gathering import shell_gather_ports
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo


def answer_through_shell_ports(
    message: str,
    session: Session,
    console: Console,
    *,
    request: AnswerRequest | None = None,
) -> LlmRunInfo | None:
    """The agent's answer stage on the shell's default port family."""
    ports = DefaultPorts(
        session=session,  # type: ignore[arg-type]
        output=ShellOutputSink(console),
        console=console,
        error_reporter=ShellErrorReporter(),
    )
    return stream_answer(
        message,
        session,
        ports.output,
        prompts=shell_prompt_context_provider(session),
        reasoning=ports.reasoning(),
        run_factory=ports.run_factory(),
        error_reporter=ports._error_reporter,  # noqa: SLF001
        request=request if request is not None else AnswerRequest(),
    )


def gather_through_shell_ports(
    message: str,
    session: Session,
    console: Console,
    *,
    agent_factory: GatherAgentFactory | None = None,
    resolved_integrations: dict[str, Any] | None = None,
) -> str | GatheredEvidence | None:
    """The agent's gather stage with the shell's gather ports."""
    ports = shell_gather_ports(session, console)
    return gather_tool_evidence(
        message,
        session,
        on_progress=ports.on_progress,
        persist=ports.persist,
        error_reporter=ShellErrorReporter(),
        agent_factory=agent_factory,
        resolved_integrations=resolved_integrations,
    )


__all__ = ["answer_through_shell_ports", "gather_through_shell_ports"]
