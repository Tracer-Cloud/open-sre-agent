"""Prompt-mediated confirmation waiting for interactive-shell turns.

A turn worker thread parks here while it waits for the user to answer a
confirmation prompt rendered by the REPL prompt loop. The wait is
cancel-safe: it polls the dispatch cancel event and raises
:class:`DispatchCancelled` instead of silently auto-confirming.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from surfaces.interactive_shell.runtime.core.state import PROMPT_REFRESH_INTERVAL_S, ReplState


class DispatchCancelled(Exception):
    """Raised when in-flight dispatch is cancelled during confirmation."""


def request_confirmation_via_prompt(
    state: ReplState,
    prompt_text: str,
    *,
    redraw: Callable[[], None] | None = None,
) -> str:
    """Park until the prompt loop delivers a y/n answer.

    ``redraw`` (typically prompt invalidate) runs after confirmation begins and
    again after it clears so the free-text typing box hides and restores
    immediately rather than waiting for the next refresh tick.
    """
    response_event = threading.Event()
    state.begin_confirmation(response_event, prompt_text)
    if redraw is not None:
        redraw()
    try:
        while not response_event.is_set():
            cancel = state.current_cancel_event
            if cancel is not None and cancel.is_set():
                raise DispatchCancelled("cancelled while awaiting confirmation")
            response_event.wait(timeout=PROMPT_REFRESH_INTERVAL_S)
        if not state.confirm_response:
            raise DispatchCancelled("cancelled while awaiting confirmation")
        return state.confirm_response[0]
    finally:
        state.clear_confirmation()
        if redraw is not None:
            redraw()


__all__ = [
    "DispatchCancelled",
    "request_confirmation_via_prompt",
]
