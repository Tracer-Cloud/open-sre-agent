"""The turn-error render adds a ``/model`` recovery hint on a credit-exhausted error."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from core.llm.shared.llm_retry import LLMCreditExhaustedError
from surfaces.interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentPresentationState,
    _render_agent_presentation_transition,
)


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str = "") -> None:
        self.lines.append(text)


def _render_turn_error(error: Exception) -> str:
    console = _RecordingConsole()
    asyncio.run(
        _render_agent_presentation_transition(
            previous=AgentPresentationState(),
            current=AgentPresentationState(),
            event=AgentEvent(type="turn_error", error=error),
            console=console,  # type: ignore[arg-type]
            spinner=MagicMock(),
        )
    )
    return "\n".join(console.lines)


def test_credit_exhausted_turn_error_shows_model_hint() -> None:
    output = _render_turn_error(LLMCreditExhaustedError("Anthropic credit exhausted"))
    assert "/model" in output


def test_other_turn_error_has_no_model_hint() -> None:
    output = _render_turn_error(RuntimeError("something else broke"))
    assert "/model" not in output
