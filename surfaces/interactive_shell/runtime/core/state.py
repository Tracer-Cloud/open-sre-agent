"""State models for the interactive shell UI runtime."""

from __future__ import annotations

import asyncio
import enum
import random
import threading
import time
from dataclasses import dataclass, field

from prompt_toolkit.application.current import get_app_or_none

from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.token_format import (
    _CHARS_PER_TOKEN,
    format_token_count_short,
)
from surfaces.shared.terminal.prompt_layout import (
    clip_prompt_text,
    prompt_line_width,
    prompt_text_width,
)

# How often prompt-toolkit refreshes prompt callbacks and confirmation polling.
PROMPT_REFRESH_INTERVAL_S = 0.25

# Default confirmation rows: (answer, label). The execution gate reads "", "y",
# "yes" as allow and "always" as allow-and-raise-auto; anything else cancels.
# The cancel row is always last so the default selection lands on it.
DEFAULT_CONFIRM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("y", "Yes, allow"),
    ("n", "No, cancel"),
)


@dataclass
class _InFlightAction:
    """One still-running tool shown (or queued) on the live action row."""

    action_id: str
    text: str
    started_at: float


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
    confirm_selected: int = 0
    confirm_options: tuple[tuple[str, str], ...] = DEFAULT_CONFIRM_OPTIONS
    plan_expanded: bool = False
    # Checklist identity for ``plan_expanded`` — step texts, ignoring status.
    plan_step_texts: tuple[str, ...] | None = None
    phase: TurnPhase = TurnPhase.IDLE
    ctrl_c_exit_hint_until: float = 0.0

    def is_dispatch_running(self) -> bool:
        return self.current_task is not None and not self.current_task.done()

    def toggle_plan_expanded(self) -> None:
        """Flip the collapsed/expanded state of the pinned plan overlay."""
        self.plan_expanded = not self.plan_expanded

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

    def arm_ctrl_c_exit_hint(self, duration_seconds: float) -> None:
        """Show the double-press exit hint without restarting the prompt."""
        self.ctrl_c_exit_hint_until = time.monotonic() + duration_seconds

    def clear_ctrl_c_exit_hint(self) -> None:
        """Remove the transient Ctrl-C exit hint."""
        self.ctrl_c_exit_hint_until = 0.0

    def is_ctrl_c_exit_hint_visible(self) -> bool:
        """Return whether the transient Ctrl-C exit hint is still active."""
        return time.monotonic() <= self.ctrl_c_exit_hint_until

    def begin_confirmation(
        self,
        event: threading.Event,
        prompt_text: str = "",
        options: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        # Reset the response list BEFORE publishing ``confirm_event`` so a
        # concurrent ``deliver_confirmation`` cannot have its answer clobbered.
        # ``phase`` is set before the publish so a parked worker is observable
        # as awaiting confirmation the instant the event is visible.
        self.confirm_response = []
        self.confirm_prompt_text = prompt_text
        self.confirm_options = options or DEFAULT_CONFIRM_OPTIONS
        # Default the arrow on the last row (cancel) so a stray Enter aborts
        # instead of approving.
        self.confirm_selected = len(self.confirm_options) - 1
        self.phase = TurnPhase.AWAITING_CONFIRMATION
        self.confirm_event = event

    def clear_confirmation(self) -> None:
        self.confirm_event = None
        self.confirm_response = []
        self.confirm_prompt_text = ""
        self.confirm_options = DEFAULT_CONFIRM_OPTIONS
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
    # One glyph advance per interval of *elapsed time*. The frame must be a
    # pure function of the clock, never of how often the prompt message
    # callback runs: prompt_toolkit evaluates the message several times per
    # render pass (layout measurement + paint), so a per-call counter can land
    # on the same frame every visible render and freeze the animation.
    _FRAME_INTERVAL_SECONDS = 0.1
    # Load-state labels for the live spinner, escalating with the turn stage:
    # waiting on the model (THINKING) → dispatching / between tools (EXECUTING)
    # → a tool is running (INVOKING_TOOLS). Each renders in a distinct accent so
    # a glance tells LLM latency from tool work.
    THINKING_PHASE = "Thinking…"
    EXECUTING_PHASE = "Executing…"
    INVOKING_TOOLS_PHASE = "Invoking tools…"
    _STOP_HINT = "(Press ESC to stop)"
    # Netrunner verb pools, escalating with time spent in the net: the longer
    # the run, the hotter the trace. Each entry maps the minimum elapsed
    # seconds to the pool active from that point on (tiers never de-escalate
    # within a turn). Entries are ordered by ascending threshold.
    _VERB_TIERS: tuple[tuple[float, tuple[str, ...]], ...] = (
        (
            0.0,  # calm run
            (
                "jacking in",
                "scanning the grid",
                "crawling the datastream",
                "riding the signal",
                "running the trace",
                "decrypting",
                "compiling daemons",
                "ghosting the subnet",
                "deep-diving the stack",
            ),
        ),
        (
            30.0,  # ICE contact
            (
                "cutting ice",
                "ICE detected… rerouting",
                "ghosting past the trace",
                "pinging black ICE",
                "running the icebreaker",
                "uploading daemons",
                "threading resonance",
            ),
        ),
        (
            90.0,  # deep run
            (
                "going past the Blackwall",
                "black ICE closing… stay frosty",
                "running Kuang Grade Mark Eleven",
                "deep in the net… trace hot",
                "riding the matrix",
            ),
        ),
    )
    # Verbs picked twice as often as the rest of their pool (default weight 1).
    _VERB_WEIGHTS = {"jacking in": 2, "crawling the datastream": 2}

    # The running action line shimmers in — a white glow rising DIM → TEXT over
    # the lead window as the action is picked up — then holds a SOLID fill while
    # it runs ("shimmer prior, solid in progress"). Level is a pure function of
    # the clock, like the spinner glyph, so it never freezes on a busy render.
    _SHIMMER_PERIOD_SECONDS = 1.1
    _SHIMMER_LEAD_SECONDS = _SHIMMER_PERIOD_SECONDS / 2  # rising half of the glow
    # The action line carries no leading glyph — just an indent under the header.
    _ACTION_INDENT = "  "

    def __init__(self) -> None:
        self.streaming: bool = False
        self.started_at: float = 0.0
        self.bytes_in: int = 0
        self._verb_tier: int = 0
        self._verb: str = self._VERB_TIERS[0][1][0]
        self.phase: str = ""
        # In-flight tools in start order. The live row shows the first still
        # running; the ReAct loop emits every start before any end, so a single
        # slot would display the last tool and clear on the first completion.
        self._in_flight_actions: list[_InFlightAction] = []

    @property
    def active_action(self) -> str:
        """Text of the first still-running action, or empty."""
        return self._in_flight_actions[0].text if self._in_flight_actions else ""

    def set_active_action(self, text: str, *, action_id: str = "") -> None:
        """Show ``text`` as a running action (theme-token shimmer).

        Distinct ``action_id`` values stack so a batched start does not
        overwrite an earlier tool. The same id updates that slot in place.
        """
        cleaned = text.strip()
        started_at = time.monotonic()
        if action_id:
            for existing in self._in_flight_actions:
                if existing.action_id == action_id:
                    existing.text = cleaned
                    existing.started_at = started_at
                    return
        self._in_flight_actions.append(
            _InFlightAction(action_id=action_id, text=cleaned, started_at=started_at)
        )

    def clear_active_action(self, action_id: str | None = None) -> None:
        """Drop one in-flight action, or all of them.

        ``None`` clears every slot. A non-empty ``action_id`` removes that
        tool only. An empty string pops the oldest slot that also omitted
        an id, so an untracked ``tool_end`` cannot wipe a named action.
        """
        if action_id is None:
            self._in_flight_actions.clear()
            return
        if action_id:
            self._in_flight_actions = [
                action for action in self._in_flight_actions if action.action_id != action_id
            ]
            return
        for index, action in enumerate(self._in_flight_actions):
            if not action.action_id:
                del self._in_flight_actions[index]
                return

    def active_action_ansi(self) -> str:
        """The indented action line, or ``""`` when none is running.

        Shimmers in (DIM → TEXT) over the lead window as the action is picked up,
        then holds a solid fill (TEXT) while it runs; scrollback keeps the
        settled copy.
        """
        current = self._in_flight_actions[0] if self._in_flight_actions else None
        if current is None:
            return ""
        elapsed = time.monotonic() - current.started_at
        if elapsed < self._SHIMMER_LEAD_SECONDS:
            # Shimmer in: the rising half of the glow (0 → 1) as it is picked up.
            phase = (elapsed % self._SHIMMER_PERIOD_SECONDS) / self._SHIMMER_PERIOD_SECONDS
            level = 1.0 - abs(2.0 * phase - 1.0)
        else:
            level = 1.0  # solid fill once in progress
        fill = ui_theme.fade_fg_ansi(level)
        lead = self._ACTION_INDENT
        text = clip_prompt_text(current.text, prompt_line_width() - prompt_text_width(lead))
        return f"{fill}{lead}{text}{ui_theme.ANSI_RESET}"

    def start(self) -> None:
        self.streaming = True
        self.started_at = time.monotonic()
        self.bytes_in = 0
        self._verb_tier = 0
        self._verb = self._pick_verb()
        self.phase = self.EXECUTING_PHASE
        self._in_flight_actions.clear()

    def advance_verb(self) -> None:
        """Pick a fresh thinking verb (the rotation cadence is the caller's).

        The agent-loop observer calls this at its chosen step boundaries so
        the label rotates during a long turn. Always picks a verb different
        from the current one so the change is visible, staying within the
        currently escalated tier.
        """
        self._verb = self._pick_verb(exclude=self._verb)

    def _tier_for_elapsed(self, elapsed: float) -> int:
        tier = 0
        for index, (threshold, _pool) in enumerate(self._VERB_TIERS):
            if elapsed >= threshold:
                tier = index
        return tier

    def _escalate_for_elapsed(self, elapsed: float) -> None:
        """Escalate the verb pool once *elapsed* crosses a tier threshold.

        One-way within a turn: the tier only moves up (``start()`` resets it).
        On a transition the verb re-rolls immediately from the new pool so
        escalation shows even during a long single LLM call with no agent-step
        events.
        """
        tier = self._tier_for_elapsed(elapsed)
        if tier > self._verb_tier:
            self._verb_tier = tier
            self._verb = self._pick_verb()

    def _pick_verb(self, exclude: str | None = None) -> str:
        pool = self._VERB_TIERS[self._verb_tier][1]
        candidates = [v for v in pool if v != exclude]
        weights = [self._VERB_WEIGHTS.get(v, 1) for v in candidates]
        return random.choices(candidates, weights=weights)[0]

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
        self._in_flight_actions.clear()

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
        hint = "/ for commands  ·  tab tool details  ·  ↑↓ history"
        app = get_app_or_none()
        if app is not None and app.current_buffer.text:
            hint += "  ·  esc to clear"
        return (
            f"{ui_theme.PROMPT_ACCENT_ANSI}Ready{ui_theme.ANSI_RESET}"
            f"{ui_theme.DIM_ANSI} · {hint}{ui_theme.ANSI_RESET}"
        )

    def _phase_accent_ansi(self) -> str:
        """Accent for the spinner lead+label, distinct per load-state phase.

        ``Thinking…`` (and pipeline stage labels) stay on the prompt accent (bold
        highlight); ``Executing…`` uses brand; ``Invoking tools…`` uses bold
        brand — the same hue as executing but heavier, so tool work reads as the
        hottest state while staying different from thinking.
        """
        if self.phase == self.INVOKING_TOOLS_PHASE:
            return ui_theme.BOLD_BRAND_ANSI
        if self.phase == self.EXECUTING_PHASE:
            return ui_theme.BRAND_ANSI
        return ui_theme.PROMPT_ACCENT_ANSI

    def inline_spinner_ansi(self) -> str:
        if not self.streaming:
            return ""
        elapsed = time.monotonic() - self.started_at
        self._escalate_for_elapsed(elapsed)
        token_count = self.bytes_in // _CHARS_PER_TOKEN
        frame_idx = int(elapsed / self._FRAME_INTERVAL_SECONDS)
        glyph = self._SPINNER_FRAMES[frame_idx % len(self._SPINNER_FRAMES)]
        if token_count > 0:
            tokens_str = format_token_count_short(token_count)
            elapsed_badge = f"[ {elapsed:.0f}s · ↓ {tokens_str} tokens]"
        else:
            elapsed_badge = f"[ {elapsed:.0f}s]"
        label = self.phase or f"{self._verb}…"
        # One prompt-region row only: a long phase (or a narrow terminal) must
        # not soft-wrap, which desyncs row height vs the one-row confirmation
        # prefix and leaves stale spinner/status lines.
        lead = f"{glyph} "
        tail = f" {self._STOP_HINT}  {elapsed_badge}"
        accent = self._phase_accent_ansi()
        width = prompt_line_width()
        reserved = prompt_text_width(lead) + prompt_text_width(tail)
        if reserved >= width:
            visible = clip_prompt_text(f"{lead}{label}{tail}", width)
            return f"{accent}{visible}{ui_theme.ANSI_RESET}"
        clipped_label = clip_prompt_text(label, width - reserved)
        return (
            f"{accent}{lead}{clipped_label}{ui_theme.ANSI_RESET}"
            f"{ui_theme.ANSI_DIM}{tail}{ui_theme.ANSI_RESET}"
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
