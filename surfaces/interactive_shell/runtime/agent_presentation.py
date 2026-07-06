"""Terminal presentation for the interactive shell agent prompt.

This module owns the **UI / presentation** side of one submitted shell prompt:
the pure presentation-state reducer, the effectful terminal transition renderer,
and the ``ConsoleAgentEventSink`` imperative shell that wires them together.

Keeping this separate from ``runtime/shell_turn_execution.py`` isolates spinner
lifecycle, prompt suppression, interruption/error messages, and stale CPR
draining from the turn's action-routing and prompt-construction logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from rich.markup import escape

from core.agent_harness.session import Session
from surfaces.interactive_shell.runtime.core.state import SpinnerState
from surfaces.interactive_shell.runtime.utils.input_policy import turn_should_show_spinner
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    WARNING,
)
from surfaces.interactive_shell.ui.components.cpr_stdin import drain_stale_cpr_bytes
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole


@dataclass(frozen=True)
class AgentEvent:
    """Agent lifecycle event emitted during one submitted shell turn."""

    type: Literal["turn_start", "turn_interrupted", "turn_error", "turn_end"]
    text: str | None = None
    error: Exception | None = None


AgentEventSink = Callable[[AgentEvent], Awaitable[None]]


@dataclass(frozen=True)
class AgentPresentationState:
    """Immutable presentation state evolved across lifecycle events."""

    show_spinner: bool = False
    prompt_suppressed: bool = False


def _reduce_agent_presentation(
    state: AgentPresentationState,
    event: AgentEvent,
    *,
    should_show_spinner: bool,
) -> AgentPresentationState:
    """Compute the next presentation state for *event* (pure)."""
    if event.type == "turn_start":
        return AgentPresentationState(
            show_spinner=should_show_spinner,
            prompt_suppressed=should_show_spinner,
        )
    if event.type == "turn_end":
        return AgentPresentationState()
    if event.type in {"turn_interrupted", "turn_error"}:
        return state
    raise ValueError(f"Unknown agent event type: {event.type!r}")


async def _render_agent_presentation_transition(
    *,
    previous: AgentPresentationState,
    current: AgentPresentationState,
    event: AgentEvent,
    console: StreamingConsole,
    spinner: SpinnerState,
) -> None:
    """Perform the terminal side effects for one presentation transition."""
    from surfaces.interactive_shell.ui.output import set_prompt_suppress_fn

    match event.type:
        case "turn_start":
            if current.show_spinner:
                spinner.start()
                set_prompt_suppress_fn(console.suppress_prompt_spinner)
        case "turn_interrupted":
            console.print(f"[{WARNING}]· interrupted[/]")
        case "turn_error":
            exc = event.error
            if exc is None:
                raise ValueError("turn_error event requires an error")
            console.print(f"[{ERROR}]turn error:[/] {escape(str(exc))}")
            # On a credit/billing wall, add the in-tool recovery hint.
            from core.llm.shared.llm_retry import LLMCreditExhaustedError

            if isinstance(exc, LLMCreditExhaustedError):
                console.print(f"[{DIM}]Run /model to switch to another provider.[/]")
        case "turn_end":
            set_prompt_suppress_fn(None)
            if previous.show_spinner:
                spinner.stop()
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()
        case _:
            raise ValueError(f"Unknown agent event type: {event.type!r}")


class ConsoleAgentEventSink:
    """Render agent lifecycle events to the terminal console.

    Imperative shell: it holds the evolving ``AgentPresentationState`` and routes
    each event through the pure ``_reduce_agent_presentation`` reducer and the
    effectful ``_render_agent_presentation_transition`` renderer.
    """

    def __init__(
        self,
        *,
        session: Session,
        spinner: SpinnerState,
        console: StreamingConsole,
    ) -> None:
        self.session = session
        self.spinner = spinner
        self.console = console
        self.state = AgentPresentationState()

    async def __call__(self, event: AgentEvent) -> None:
        previous = self.state
        self.state = _reduce_agent_presentation(
            previous,
            event,
            should_show_spinner=turn_should_show_spinner(event.text or "", self.session),
        )
        await _render_agent_presentation_transition(
            previous=previous,
            current=self.state,
            event=event,
            console=self.console,
            spinner=self.spinner,
        )


__all__ = [
    "AgentEvent",
    "AgentPresentationState",
    "ConsoleAgentEventSink",
    "AgentEventSink",
]
