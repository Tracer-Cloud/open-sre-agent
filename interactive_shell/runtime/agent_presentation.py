"""Terminal presentation for the interactive shell agent prompt.

This module owns the **UI / presentation** side of one submitted shell prompt:
the pure presentation-state reducer, the effectful terminal transition renderer,
the ``ConsoleAgentEventSink`` imperative shell that wires them together, and the
JSON-like assistant response renderer.

Keeping this separate from ``harness/agent.py`` isolates spinner lifecycle,
prompt suppression, markdown rendering, interruption/error messages, and stale
CPR draining from the turn's action-routing and prompt-construction logic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from interactive_shell.harness.events import AgentEvent, AsyncAgentEventSink
from interactive_shell.harness.llm_context.session import ReplSession
from interactive_shell.runtime.core.state import SpinnerState
from interactive_shell.runtime.utils.input_policy import turn_should_show_spinner
from interactive_shell.ui import (
    BOLD_BRAND,
    ERROR,
    MARKDOWN_THEME,
    STREAM_LABEL_ASSISTANT,
    WARNING,
)
from interactive_shell.ui.components.cpr_stdin import drain_stale_cpr_bytes
from interactive_shell.ui.streaming.console import StreamingConsole


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
    if event.type == "prompt_start":
        return AgentPresentationState(
            show_spinner=should_show_spinner,
            prompt_suppressed=should_show_spinner,
        )
    if event.type == "prompt_end":
        return AgentPresentationState()
    if event.type in {"prompt_interrupted", "prompt_error", "agent_start", "agent_stop"}:
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
    from interactive_shell.ui.output import set_prompt_suppress_fn

    match event.type:
        case "prompt_start":
            if current.show_spinner:
                spinner.start()
                set_prompt_suppress_fn(console.suppress_prompt_spinner)
        case "prompt_interrupted":
            console.print(f"[{WARNING}]· interrupted[/]")
        case "prompt_error":
            exc = event.error
            if exc is None:
                raise ValueError("prompt_error event requires an error")
            console.print(f"[{ERROR}]prompt error:[/] {escape(str(exc))}")
        case "prompt_end":
            set_prompt_suppress_fn(None)
            if previous.show_spinner:
                spinner.stop()
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()
        case "agent_start" | "agent_stop":
            return
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
        session: ReplSession,
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


def render_json_like_response(console: Console, text: str) -> None:
    """Render a JSON-looking assistant response as markdown (fallback path)."""
    if not text.lstrip().startswith("{") or not text.strip():
        return

    console.print()
    console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
    with console.use_theme(MARKDOWN_THEME):
        console.print(Markdown(text, code_theme="ansi_dark"))
    console.print()


__all__ = [
    "AgentEvent",
    "AsyncAgentEventSink",
    "AgentPresentationState",
    "ConsoleAgentEventSink",
    "render_json_like_response",
]
