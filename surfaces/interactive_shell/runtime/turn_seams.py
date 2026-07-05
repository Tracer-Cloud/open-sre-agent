"""Injection contracts for the interactive-shell turn seams.

These protocols describe exactly what ``execute_shell_turn`` requires from the
action / gather / answer adapters it composes, so an injected test double is
checked at type-time rather than at runtime. The default adapters
(``action_turn``, ``answer_turn``, ``integration_tool_gathering``) satisfy them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from rich.console import Console

from core.agent_harness.models.turn_results import ToolCallingTurnResult
from core.agent_harness.ports import OutputSink
from core.agent_harness.session import Session
from core.agent_harness.turns.turn_plan import TurnPlan
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.utils.telemetry import LlmRunInfo


class RunActionToolTurn(Protocol):
    """Action-selection seam driven by ``execute_shell_turn``.

    ``deps`` is intentionally not part of the contract: ``execute_shell_turn``
    never injects it, and the default adapter supplies its own LLM factory.
    """

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        request_exit: Callable[[], None] | None = None,
        turn_plan: TurnPlan | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> ToolCallingTurnResult:
        """Run one action turn and return its facts."""


class GatherEvidence(Protocol):
    """Gather seam: collect read-only integration evidence, or None."""

    def __call__(
        self,
        message: str,
        session: Session,
        console: Console,
        *,
        is_tty: bool | None = None,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> str | None:
        """Gather evidence for the message, or return None when nothing applies."""


class AnswerKwargs(TypedDict, total=False):
    """Keyword args ``run_turn`` forwards to the answer seam (all optional).

    ``total=False`` mirrors ``run_turn`` omitting ``tool_observation_on_screen``
    on the plain (no-evidence) path.
    """

    confirm_fn: Callable[[str], str] | None
    is_tty: bool | None
    tool_observation: str | None
    tool_observation_on_screen: bool
    turn_plan: TurnPlan | None


class AnswerShellQuestion(Protocol):
    """Answer seam: respond via the grounded conversational assistant."""

    def __call__(
        self,
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
        """Answer the question, returning the LLM run info or None."""


__all__ = [
    "AnswerKwargs",
    "AnswerShellQuestion",
    "GatherEvidence",
    "RunActionToolTurn",
]
