"""State models for the interactive shell UI runtime."""

from __future__ import annotations

import asyncio
import enum
import random
import threading
import time
from dataclasses import dataclass, field

from prompt_toolkit.application.current import get_app_or_none

from platform.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.components.token_format import (
    _CHARS_PER_TOKEN,
    format_token_count_short,
)

# How often prompt-toolkit refreshes prompt callbacks and confirmation polling.
PROMPT_REFRESH_INTERVAL_S = 0.25


class TurnPhase(enum.Enum):
    """Explicit lifecycle phase of the current interactive-shell turn.

    ``phase`` is the declared turn intent and is authoritative for the
    confirmation and cancelling states. ``is_dispatch_running()`` remains
    derived from the asyncio task (the runtime truth of the in-flight turn),
    because a task can settle on its own without an explicit transition.
    """

    IDLE = "idle"
    DISPATCHING = "dispatching"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCELLING = "cancelling"


@dataclass
class ReplState:
    """Shared runtime state for prompt loop, queue worker, and cancel handlers.

    Single source of truth for the active dispatch task, cancellation event,
    confirmation lifecycle, exit request, and the explicit ``TurnPhase``.
    Mutate turn state through the transition methods below rather than poking
    raw fields, so ``phase`` stays consistent with the cancellation primitives.
    """

    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    current_task: asyncio.Task[None] | None = None
    current_cancel_event: threading.Event | None = None
    loop: asyncio.AbstractEventLoop | None = None
    exit_requested: bool = False
    confirm_event: threading.Event | None = None
    confirm_response: list[str] = field(default_factory=list)
    confirm_prompt_text: str = ""
    phase: TurnPhase = TurnPhase.IDLE

    def is_dispatch_running(self) -> bool:
        return self.current_task is not None and not self.current_task.done()

    def is_awaiting_confirmation(self) -> bool:
        return self.phase is TurnPhase.AWAITING_CONFIRMATION

    def is_cancelling(self) -> bool:
        return self.phase is TurnPhase.CANCELLING

    def deliver_confirmation(self, answer: str) -> None:
        if self.confirm_event is None:
            return
        self.confirm_response.append(answer)
        self.confirm_event.set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def request_exit(self) -> None:
        self.exit_requested = True

    def begin_confirmation(self, event: threading.Event, prompt_text: str = "") -> None:
        # Reset the response list BEFORE publishing ``confirm_event`` so a
        # concurrent ``deliver_confirmation`` cannot have its answer clobbered.
        # ``phase`` is set before the publish so a parked worker is observable
        # as awaiting confirmation the instant the event is visible.
        self.confirm_response = []
        self.confirm_prompt_text = prompt_text
        self.phase = TurnPhase.AWAITING_CONFIRMATION
        self.confirm_event = event

    def clear_confirmation(self) -> None:
        self.confirm_event = None
        self.confirm_response = []
        self.confirm_prompt_text = ""
        # Only a normal confirmation completion returns to dispatching/idle; a
        # cancel in progress must keep its CANCELLING phase.
        if self.phase is TurnPhase.AWAITING_CONFIRMATION:
            self.phase = TurnPhase.DISPATCHING if self.is_dispatch_running() else TurnPhase.IDLE

    def start_dispatch(self, *, task: asyncio.Task[None], cancel_event: threading.Event) -> None:
        self.current_task = task
        self.current_cancel_event = cancel_event
        self.phase = TurnPhase.DISPATCHING

    def attach_turn_task(self, task: asyncio.Task[None]) -> None:
        """Mark a queued turn task as the active dispatch (queue worker entry)."""
        self.current_task = task
        self.phase = TurnPhase.DISPATCHING

    def attach_cancel_event(self, cancel_event: threading.Event) -> None:
        """Park a cancel event for a dispatch that has no asyncio task."""
        self.current_cancel_event = cancel_event
        self.phase = TurnPhase.DISPATCHING

    def clear_current_task(self, task: asyncio.Task[None] | None = None) -> None:
        if task is None or self.current_task is task:
            self.current_task = None
            self.phase = TurnPhase.IDLE

    def finish_dispatch(self, cancel_event: threading.Event) -> None:
        if self.current_cancel_event is cancel_event:
            self.current_cancel_event = None
        self.phase = TurnPhase.IDLE

    def cancel_current_dispatch(self) -> None:
        # Mark the cancel intent first, but only when there is something to
        # cancel, so an idle no-op call does not leave a stale CANCELLING phase.
        if (
            self.current_cancel_event is not None
            or self.confirm_event is not None
            or self.is_dispatch_running()
        ):
            self.phase = TurnPhase.CANCELLING
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        if self.confirm_event is not None:
            self.confirm_event.set()
        task = self.current_task
        if task is not None and not task.done():
            if self.loop is not None:
                self.loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()


class SpinnerState:
    """Mutable state read by prompt callbacks for toolbar + inline spinner."""

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _THINKING_VERBS = (
        "thinking",
        "pondering",
        "exploring",
        "reasoning",
        "considering",
        "analysing",
        "investigating",
        "deliberating",
        "ruminating",
        "deducing",
        "noodling",
    )

    def __init__(self) -> None:
        self.streaming: bool = False
        self.started_at: float = 0.0
        self.bytes_in: int = 0
        self._frame_idx: int = 0
        self._verb: str = self._THINKING_VERBS[0]
        self.phase: str = ""

    def start(self) -> None:
        self.streaming = True
        self.started_at = time.monotonic()
        self.bytes_in = 0
        self._frame_idx = 0
        self._verb = random.choice(self._THINKING_VERBS)
        self.phase = ""

    def set_phase(self, label: str) -> None:
        """Animate a caller-supplied phase label instead of a thinking verb.

        Investigation stages (``/investigate``) dispatch deterministically, so
        the turn-level "thinking" spinner never starts. The progress display
        calls this to keep the prompt spinner cycling with the active pipeline
        stage; it can be called repeatedly to advance the phase.
        """
        if not self.streaming:
            self.started_at = time.monotonic()
            self._frame_idx = 0
        self.streaming = True
        self.phase = label

    def stop(self) -> None:
        self.streaming = False
        self.phase = ""

    def toolbar_ansi(self) -> str:
        # Always return an empty string so prompt_toolkit's ConditionalContainer
        # collapses the toolbar in every state.  A visible toolbar causes
        # prompt_toolkit to emit \033[6n (CPR) cursor-position queries on every
        # refresh_interval; those responses leak into the vt100 input parser as
        # literal keystrokes, corrupting the input field.  Hiding the toolbar
        # unconditionally also keeps its height at zero in both streaming and
        # idle states, which prevents the one-row height delta that would cause
        # prompt_toolkit to misplace the cursor and leave stale spinner lines on
        # screen.  Idle hints are surfaced through idle_hint_ansi() instead,
        # which is rendered in the prompt message's reserved first line.
        return ""

    def idle_hint_ansi(self) -> str:
        """Dim hint line shown above the rule when no dispatch is running."""
        hint = "/ for commands  ·  ↑↓ history"
        app = get_app_or_none()
        if app is not None and app.current_buffer.text:
            hint += "  ·  esc to clear"
        return f"{ui_theme.DIM_ANSI}{hint}{ui_theme.ANSI_RESET}"

    def inline_spinner_ansi(self) -> str:
        if not self.streaming:
            return ""
        elapsed = time.monotonic() - self.started_at
        token_count = self.bytes_in // _CHARS_PER_TOKEN
        glyph = self._SPINNER_FRAMES[self._frame_idx % len(self._SPINNER_FRAMES)]
        self._frame_idx += 1
        if token_count > 0:
            tokens_str = format_token_count_short(token_count)
            suffix = f" ({elapsed:.0f}s · ↓ {tokens_str} tokens)"
        else:
            suffix = f" ({elapsed:.0f}s)"
        label = self.phase or f"{self._verb}…"
        return (
            f"{ui_theme.PROMPT_ACCENT_ANSI}{glyph} {label}{ui_theme.ANSI_RESET}"
            f"{ui_theme.ANSI_DIM}{suffix}  esc to cancel{ui_theme.ANSI_RESET}"
        )


@dataclass(frozen=True)
class ReplMutableState:
    """Initial mutable state bundle shared by the interactive runtime."""

    state: ReplState
    spinner: SpinnerState


def create_repl_mutable_state(
    *,
    state: ReplState | None = None,
    spinner: SpinnerState | None = None,
) -> ReplMutableState:
    """Return the canonical initial mutable state objects for a REPL runtime."""
    return ReplMutableState(
        state=state if state is not None else ReplState(),
        spinner=spinner if spinner is not None else SpinnerState(),
    )


__all__ = [
    "PROMPT_REFRESH_INTERVAL_S",
    "ReplMutableState",
    "ReplState",
    "SpinnerState",
    "TurnPhase",
    "create_repl_mutable_state",
]
