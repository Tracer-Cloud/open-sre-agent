"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal.

Built on a per-turn :func:`PromptSession.prompt_async` cycle wrapped in
:func:`patch_stdout`. The prompt is pinned at the bottom of the terminal,
streamed responses print into normal terminal output above it (so they
flow into native scrollback — the user can scroll the terminal naturally
to see prior turns), and a dynamic ``bottom_toolbar`` shows the live
``thinking… (Ns · ↓ X tokens) — esc to interrupt`` indicator while a
turn is generating.

Type-ahead during streaming works because the dispatch runs as an
``asyncio`` background task; the next iteration's ``prompt_async``
starts immediately, so the input frame stays editable while output
continues to flow above it.

An earlier prototype used a textual-based persistent app with an inline
``RichLog``. That fought with macOS Terminal.app's native scroll
(content stayed inside a widget instead of going to terminal scrollback)
and with the expectation of a single, native scroll axis. The
``prompt_toolkit`` + ``patch_stdout`` shape — which is how Claude Code
behaves — gives up the declarative widget tree but matches terminal
conventions: input pinned at bottom, history scrolls naturally.
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.orchestration.agent_actions as _agent_actions
from app.agents.sweep import run_startup_sweep
from app.analytics.cli import capture_terminal_turn_summarized
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell import commands as _commands
from app.cli.interactive_shell.chat import cli_agent as _cli_agent
from app.cli.interactive_shell.chat import cli_help as _cli_help
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.prompting import follow_up as _follow_up
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.routing import router as _router
from app.cli.interactive_shell.runtime import HotReloadCoordinator, ReplSession, TaskRegistry
from app.cli.interactive_shell.ui import (
    ANSI_DIM,
    ANSI_RESET,
    DIM,
    ERROR,
    PROMPT_ACCENT_ANSI,
    WARNING,
    render_banner,
)
from app.cli.interactive_shell.ui.streaming import (
    _CHARS_PER_TOKEN,
    format_token_count_short,
)
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception
from app.cli.support.prompt_support import repl_prompt_note_ctrl_c, repl_reset_ctrl_c_gate
from app.llm_reasoning_effort import apply_reasoning_effort

# Module-alias pattern (introduced by main for testability + hot-reload).
# Local rebindings expose the same names the rest of this module uses.
_build_prompt_session = _prompt_surface._build_prompt_session
_prompt_message = _prompt_surface._prompt_message
render_submitted_prompt = _prompt_surface.render_submitted_prompt
route_input = _router.route_input
answer_cli_help = _cli_help.answer_cli_help
answer_cli_agent = _cli_agent.answer_cli_agent
answer_follow_up = _follow_up.answer_follow_up
execute_cli_actions_with_metrics = _agent_actions.execute_cli_actions_with_metrics
dispatch_slash = _commands.dispatch_slash

_INTERVENTION_CORRECTION_RE = re.compile(
    r"("
    r"no(?=[,.!?]|$)"
    r"|nope\b"
    r"|nvm\b"
    r"|nevermind\b|never\s*mind\b"
    r"|wrong\b"
    r"|wait(?=[,.!?]|$)"
    r"|stop(?=[,.!?]|$)"
    r"|actually\b"
    r"|scratch\s+that\b"
    r"|instead(?=[,.!?]|$)"
    r"|(?:let'?s\s+)?do\s+[^.\n]{1,60}\s+instead\b"
    r"|try\s+[^.\n]{1,60}\s+instead\b"
    r")",
    re.IGNORECASE,
)


# Tokens that count as an explicit answer to a ``Proceed? [Y/n]``
# confirmation. Compared against ``text.strip().lower()`` so case and
# trailing whitespace don't matter. ``""`` is included because the
# upstream prompt is ``[Y/n]`` (capital Y) and ``execution_policy``
# treats an empty answer as "yes" — see
# :func:`app.cli.interactive_shell.orchestration.execution_policy.execution_allowed`.
_CONFIRMATION_TOKENS: frozenset[str] = frozenset({"", "y", "yes", "n", "no"})


def _looks_like_confirmation_answer(text: str | None) -> bool:
    """True when ``text`` reads as a deliberate y/n answer to a pending
    confirmation prompt (rather than type-ahead the user intended as a
    new turn).

    The check is conservative: only the explicit y/n tokens (and the
    empty string, which the upstream ``[Y/n]`` prompt treats as "yes")
    qualify. Anything else — a question, a sentence, a slash command —
    is treated as a normal turn so a user who typed ahead while an
    action plan was still being parsed doesn't silently decline the
    pending action.
    """
    return (text or "").strip().lower() in _CONFIRMATION_TOKENS


def _looks_like_correction(text: str) -> bool:
    """True when text begins with a short correction cue (intervention signal)."""
    stripped = text.lstrip()
    if not stripped or stripped.startswith("```"):
        return False
    return _INTERVENTION_CORRECTION_RE.match(stripped[:80]) is not None


def _run_new_alert(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.cli.interactive_shell.orchestration.execution_policy import (
        evaluate_investigation_launch,
        execution_allowed,
    )
    from app.cli.interactive_shell.runtime.tasks import TaskKind
    from app.cli.investigation import run_investigation_for_session

    policy = evaluate_investigation_launch(action_type="investigation")
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary="run RCA investigation from pasted alert text",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    ):
        session.record("alert", text, ok=False)
        return

    task = session.task_registry.create(TaskKind.INVESTIGATION, command="free-text investigation")
    task.mark_running()
    try:
        with apply_reasoning_effort(session.reasoning_effort):
            final_state = run_investigation_for_session(
                alert_text=text,
                context_overrides=session.accumulated_context or None,
                cancel_requested=task.cancel_requested,
            )
    except KeyboardInterrupt:
        task.mark_cancelled()
        session.record_intervention("ctrl_c")
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", text, ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.new_alert")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)


def _dispatch_one_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
) -> None:
    """Route + dispatch one accepted line. Pure synchronous body.

    Used both from :func:`_run_one_dispatch` (wrapped in
    ``asyncio.to_thread`` so the worker runs off the prompt-toolkit
    main thread) and from the ``initial_input`` pre-seeding path.
    ``on_exit`` is called when a slash command requests REPL exit
    (e.g. ``/exit``); the caller decides what that means (in the
    interactive path, ``app.exit()``; in the pre-seeded path, an early
    return).

    LLM-bound branches (``cli_help``, ``cli_agent``, ``follow_up``) are
    wrapped in :func:`apply_reasoning_effort` so the per-session
    ``/effort`` setting from main propagates into the model call.
    """
    decision = route_input(text, session)
    kind = decision.route_kind.value
    session.last_route_decision = decision
    get_analytics().capture(
        Event.INTERACTIVE_SHELL_ROUTE_DECISION,
        decision.to_event_payload(),
    )
    if kind in ("follow_up", "new_alert") and _looks_like_correction(text):
        session.record_intervention("correction")

    if kind == "slash":
        # Rewrite bare-word commands to their slash form before dispatch.
        cmd_text = _router.slash_dispatch_text(text)
        try:
            should_continue = dispatch_slash(cmd_text, session, console)
        except Exception as exc:
            report_exception(exc, context="interactive_shell.slash_dispatch")
            console.print(
                f"[{ERROR}]command error:[/] {escape(str(exc))}"
                f" [{DIM}](the REPL is still running)[/]"
            )
            should_continue = True
        if not should_continue:
            on_exit()
        return

    if kind == "cli_help":
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return

    if kind == "cli_agent":
        turn = execute_cli_actions_with_metrics(text, session, console, confirm_fn=confirm_fn)
        fallback_to_llm = not turn.handled
        snapshot = session.record_terminal_turn(
            executed_count=turn.executed_count,
            executed_success_count=turn.executed_success_count,
            fallback_to_llm=fallback_to_llm,
        )
        capture_terminal_turn_summarized(
            planned_count=turn.planned_count,
            executed_count=turn.executed_count,
            executed_success_count=turn.executed_success_count,
            fallback_to_llm=fallback_to_llm,
            session_turn_index=snapshot.turn_index,
            session_fallback_count=snapshot.fallback_count,
            session_action_success_percent=snapshot.action_success_percent,
            session_fallback_rate_percent=snapshot.fallback_rate_percent,
        )
        if turn.handled:
            return
        with apply_reasoning_effort(session.reasoning_effort):
            answer_cli_agent(text, session, console, confirm_fn=confirm_fn)
        session.record("cli_agent", text)
        return

    if kind == "new_alert":
        _run_new_alert(text, session, console, confirm_fn=confirm_fn)
        return

    # follow_up — grounded answer against session.last_state
    with apply_reasoning_effort(session.reasoning_effort):
        answer_follow_up(text, session, console)
    session.record("follow_up", text)


def _run_initial_input(
    initial_input: str,
    session: ReplSession,
    hot_reloader: HotReloadCoordinator | None = None,
) -> int:
    """Test-harness path — drain pre-seeded input through the same dispatch logic.

    ``hot_reloader`` (introduced in main) is consulted before each
    seeded line so dev-time module changes are picked up between turns
    even in the seeded-input flow.
    """
    console = Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_banner(console)
    exit_requested = [False]

    def _early_exit() -> None:
        exit_requested[0] = True

    for line in initial_input.splitlines():
        if hot_reloader is not None:
            hot_reloader.check_and_reload(console)
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        _dispatch_one_turn(stripped, session, console, on_exit=_early_exit)
        if exit_requested[0]:
            return 0
    return 0


async def _repl_main(
    initial_input: str | None = None,
    _config: ReplConfig | None = None,
) -> int:
    """Async REPL entrypoint — wires session, prompt history, persistent
    task registry, and the optional :class:`HotReloadCoordinator` before
    delegating to either :func:`_run_initial_input` (seeded path) or
    :func:`_run_interactive` (interactive prompt-toolkit path).

    Tests reach this directly with seeded input + a custom config; the
    public :func:`run_repl` wraps it in ``asyncio.run`` for the CLI
    entrypoint.
    """
    cfg = _config or ReplConfig.load()
    session = ReplSession()
    session.task_registry = TaskRegistry.persistent()
    pt_session = _build_prompt_session()
    session.prompt_history_backend = pt_session.history
    hot_reloader = HotReloadCoordinator() if cfg.reload else None

    if initial_input:
        return _run_initial_input(initial_input, session, hot_reloader)

    # Pass the already-built ``pt_session`` so ``_run_interactive``
    # doesn't allocate a second one. ``session.prompt_history_backend``
    # is already wired above.
    await _run_interactive(session, hot_reloader, pt_session=pt_session)
    return 0


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    """Enter the interactive REPL. Returns the exit code."""
    cfg = config or ReplConfig.load()

    if not cfg.enabled:
        return 0

    if not sys.stdin.isatty() and initial_input is None:
        # In non-TTY contexts (piped input, CI), don't start an interactive loop.
        # Callers should use `opensre investigate` instead.
        return 0

    # Prune dead-PID agent records and stale lockfiles before the REPL
    # starts. Errors are caught inside; a sweep failure must never prevent
    # the REPL from starting.
    run_startup_sweep()

    # Banner prints to real stdout (interactive mode only — the seeded
    # path renders its own console). It lives in the user's terminal
    # scrollback above all subsequent turns, so native terminal scroll
    # reveals everything from the session top downward.
    if not initial_input:
        real_console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        render_banner(real_console)

    try:
        return asyncio.run(_repl_main(initial_input=initial_input, _config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


# How often the prompt's ``bottom_toolbar`` and ``message`` callables
# re-evaluate. 250 ms (4 Hz) is the sweet spot: the spinner still feels
# alive, the token counter visibly increments, and the bottom area
# settles enough that incoming Markdown chunks streaming above the
# input no longer feel like they're "fighting" with a busy toolbar
# redraw at 10 Hz. The streaming layer keeps its own 100 ms cadence
# for the byte-count update hook so the counter stays accurate; the
# *visible* refresh is what's throttled here.
#
# Also reused by :func:`_route_confirm_through_prompt` as the
# ``confirm_event.wait`` poll timeout. 250 ms is still well below the
# threshold where humans notice Esc-cancel latency.
_PROMPT_REFRESH_INTERVAL_S = 0.25


@dataclass
class _ReplState:
    """REPL session state shared between the prompt loop, the queue
    processor, and the cancel/exit key bindings.

    Replaces the dict-cell idiom (``current_dispatch = {"task": None}``)
    with a single explicit owner of the cancellation primitives. Methods
    expose intent (``cancel_current_dispatch``, ``is_dispatch_running``)
    so callers don't have to re-derive it from the raw fields.
    """

    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    current_task: asyncio.Task[None] | None = None
    # The currently-active dispatch's cancel event. Each turn allocates
    # a fresh ``threading.Event`` inside :func:`_run_one_dispatch` and
    # parks it here so :meth:`cancel_current_dispatch` can flip it. A
    # *previous* turn's worker thread keeps polling its own (already
    # released) event, so a fresh ``Event.clear()`` for the new turn
    # never races with a still-draining iterator from the previous one.
    current_cancel_event: threading.Event | None = None
    # The asyncio loop running the prompt coroutine. Bound by
    # :func:`_run_interactive` once the loop is up so
    # :meth:`cancel_current_dispatch` can route ``Task.cancel()`` through
    # ``call_soon_threadsafe``. ``asyncio.Task.cancel`` is documented as
    # not thread-safe, and this method is invoked from worker threads
    # via :func:`_request_exit` (the ``/exit`` slash handler runs in
    # ``asyncio.to_thread``). ``call_soon_threadsafe`` is itself safe
    # from any thread, including the loop thread, so we use it
    # uniformly rather than branching on caller-thread identity.
    loop: asyncio.AbstractEventLoop | None = None
    exit_requested: bool = False
    # Confirmation routing: when an in-flight dispatch needs ``Proceed?
    # [y/N]`` input, it parks ``confirm_event`` + ``confirm_response``
    # here. The main prompt loop checks them after each ``prompt_async``
    # return and, if a confirmation is pending, delivers the typed text
    # to the worker thread instead of queueing a new turn.
    confirm_event: threading.Event | None = None
    confirm_response: list[str] = field(default_factory=list)

    def is_dispatch_running(self) -> bool:
        return self.current_task is not None and not self.current_task.done()

    def is_awaiting_confirmation(self) -> bool:
        return self.confirm_event is not None

    def deliver_confirmation(self, answer: str) -> None:
        """Hand the user's typed text to the parked worker thread."""
        if self.confirm_event is None:
            return
        self.confirm_response.append(answer)
        self.confirm_event.set()

    def cancel_current_dispatch(self) -> None:
        """Signal cancellation through both channels.

        The active dispatch's per-turn ``current_cancel_event`` is what
        stops the streaming loop in :func:`stream_to_console` (worker
        thread); ``Task.cancel()`` is what unblocks the asyncio waiter
        in :func:`_run_one_dispatch` (main thread). Both are needed.
        Also unparks any worker thread waiting on a confirmation prompt
        so it doesn't hang on Esc.

        ``Task.cancel()`` is scheduled via ``call_soon_threadsafe``
        because this method is reachable from worker threads (e.g.
        :func:`_request_exit`, which the ``/exit`` handler invokes from
        an ``asyncio.to_thread`` worker), and ``asyncio.Task.cancel`` is
        not thread-safe. ``call_soon_threadsafe`` is safe from any
        thread including the loop thread, so the same call works
        regardless of who invokes us. If no loop has been bound yet
        (e.g. in unit tests that drive ``cancel_current_dispatch``
        synchronously before :func:`_run_interactive` runs), fall back
        to a direct call.
        """
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


class _SpinnerState:
    """Mutable state read by the prompt's bottom-toolbar callback.

    The toolbar callback runs every ``refresh_interval`` (~100 ms) while a
    turn streams; it reads ``streaming``, ``started_at``, ``bytes_in`` to
    compose the live ``⠋ thinking… (Ns · ↓ X tokens)`` line. Streaming
    layer (:mod:`streaming`) updates ``bytes_in`` via a console hook —
    see :class:`_StreamingConsole` below.
    """

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    # Claude Code-style verb rotation — one verb is picked per turn so
    # the indicator doesn't always say the same word. Adds personality
    # without flicker (the verb stays fixed for the whole turn).
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

    def start(self) -> None:
        self.streaming = True
        self.started_at = time.monotonic()
        self.bytes_in = 0
        self._frame_idx = 0
        # Pick a fresh verb per turn — stays constant for the duration
        # so the indicator doesn't flicker between words mid-stream.
        self._verb = random.choice(self._THINKING_VERBS)

    def stop(self) -> None:
        self.streaming = False

    def toolbar_ansi(self) -> ANSI:
        """Bottom toolbar — single hint row directly below the input.

        Always one row tall regardless of streaming state. Keeping
        the toolbar a constant height is what stops the input cursor
        from jumping up/down by a line when streaming starts or stops
        (the spinner lives above the input now, in the prompt
        message — see :func:`_message_with_spinner`).

        Hint text: ``esc to interrupt`` while a turn is streaming,
        otherwise ``/ for commands  ·  ↑↓ history`` with
        ``  ·  esc to clear`` appended only when the input buffer
        actually has text. The Esc handler is a no-op on empty buffer
        (see :func:`_build_cancel_key_bindings`), so advertising the
        clear shortcut unconditionally was misleading.
        """
        if self.streaming:
            hint = "esc to interrupt"
        else:
            hint = "/ for commands  ·  ↑↓ history"
            app = get_app_or_none()
            # ``Application.current_buffer`` always returns a Buffer
            # (returns a dummy if no focus), so direct access is safe
            # — no None guard needed beyond the app-None check.
            if app is not None and app.current_buffer.text:
                hint += "  ·  esc to clear"
        return ANSI(f"{ANSI_DIM}{hint}{ANSI_RESET}")

    def inline_spinner_ansi(self) -> str:
        """Single-line ``⠋ thinking… (Ns · ↓ X tokens)`` indicator, or
        empty string when not streaming. Consumed by
        :func:`_message_with_spinner` and pinned above the input rule,
        so the spinner appears at the visual end of the response
        stream while the input cursor stays anchored below it.
        """
        if not self.streaming:
            return ""
        elapsed = time.monotonic() - self.started_at
        tokens_str = format_token_count_short(self.bytes_in // _CHARS_PER_TOKEN)
        glyph = self._SPINNER_FRAMES[self._frame_idx % len(self._SPINNER_FRAMES)]
        self._frame_idx += 1
        return (
            f"{PROMPT_ACCENT_ANSI}{glyph} {self._verb}…{ANSI_RESET}"
            f"{ANSI_DIM} ({elapsed:.0f}s · ↓ {tokens_str} tokens){ANSI_RESET}"
        )


class _StreamingConsole(Console):
    """``rich.Console`` that exposes ``update_streaming_progress`` and
    ``cancel_requested`` to :func:`stream_to_console`. The streaming
    layer keys off the presence of these via ``getattr`` to (a) push
    live byte counts into the spinner state and (b) stop pulling LLM
    chunks when the user presses Esc — ``asyncio.to_thread`` doesn't
    propagate task cancellation into the worker thread, so without
    this signal the dispatch keeps streaming after Esc.
    """

    def __init__(
        self,
        spinner: _SpinnerState,
        cancel_event: threading.Event,
        **kwargs: Any,
    ) -> None:
        # ``**kwargs: Any`` (rather than ``object``) so we don't need a
        # ``# type: ignore`` on the super call — Rich's Console accepts
        # a wide kwargs surface (highlight, force_terminal, color_system,
        # legacy_windows, …) that mypy can't narrow from a generic
        # ``object`` mapping.
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event

    def update_streaming_progress(self, bytes_received: int) -> None:
        # Plain attribute write — read by ``_SpinnerState.toolbar_ansi``
        # on the next ``refresh_interval`` repaint (every 100 ms). No
        # cross-thread synchronisation needed; the dispatch worker
        # writes, the prompt-toolkit app reads, and 100 ms staleness on
        # the token counter is imperceptible.
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()


async def _run_interactive(
    session: ReplSession,
    hot_reloader: HotReloadCoordinator | None = None,
    pt_session: PromptSession[str] | None = None,
) -> None:
    """Per-turn ``prompt_async`` cycle backed by a queue + background
    processor. Submitting a new prompt while a turn is streaming
    **enqueues** it — the active turn finishes naturally and the queued
    item runs next (matches Claude Code's behaviour). ``Esc`` cancels
    just the currently-running dispatch; the processor moves on to the
    next queued item.

    Type-ahead during streaming works because ``prompt_async`` keeps
    running on the main coroutine while the processor drains the queue
    in the background — the user can type and queue further prompts
    without waiting for the active turn to complete.

    ``hot_reloader`` (from main, optional) is consulted at the start of
    each prompt iteration so dev-time edits to dispatch handlers are
    picked up between turns.

    ``pt_session`` may be supplied by the caller (``_repl_main`` already
    built one to wire ``session.prompt_history_backend``) to avoid a
    redundant ``_build_prompt_session()`` call here. When ``None``, this
    function builds its own — keeps the public signature usable for
    callers that don't pre-build.
    """
    if pt_session is None:
        pt_session = _build_prompt_session()
        session.prompt_history_backend = pt_session.history
    spinner = _SpinnerState()
    state = _ReplState()

    cancel_kb = _build_cancel_key_bindings(state)
    _install_session_key_bindings(pt_session, cancel_kb)

    # Capture the prompt-toolkit ``Application`` and the running asyncio
    # loop on the main coroutine. ``_request_exit`` is invoked from the
    # dispatch *worker thread* (via ``_dispatch_one_turn``'s ``on_exit``
    # when a slash command like ``/exit`` returns False); from there
    # ``get_app_or_none()`` returns ``None`` because the worker thread
    # never had the prompt_toolkit ``_current_app`` ContextVar. Cached
    # references + ``call_soon_threadsafe`` avoid that ContextVar
    # dependency so ``/exit`` actually dismisses the prompt instead of
    # leaving the user staring at an idle one until they hit Enter.
    pt_app = pt_session.app
    main_loop = asyncio.get_running_loop()
    # Bind the loop so :meth:`_ReplState.cancel_current_dispatch` can
    # route ``Task.cancel`` through ``call_soon_threadsafe`` when it's
    # invoked from a worker thread (see :func:`_request_exit`).
    state.loop = main_loop

    def _request_exit() -> None:
        state.exit_requested = True
        state.cancel_current_dispatch()
        main_loop.call_soon_threadsafe(pt_app.exit)

    async def _run_one_dispatch(text: str) -> None:
        # Per-turn cancel event — fresh ``threading.Event`` so a worker
        # thread from a previous turn (still draining its iterator at
        # cancel time) keeps polling its OWN event and never observes a
        # cleared shared one. ``cancel_current_dispatch`` flips this
        # event via ``state.current_cancel_event``.
        dispatch_cancel = threading.Event()
        state.current_cancel_event = dispatch_cancel
        console = _StreamingConsole(
            spinner,
            dispatch_cancel,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        spinner.start()
        try:
            await asyncio.to_thread(
                _dispatch_one_turn,
                text,
                session,
                console,
                on_exit=_request_exit,
                confirm_fn=lambda prompt: _route_confirm_through_prompt(state, prompt),
            )
        except asyncio.CancelledError:
            console.print(f"[{WARNING}]· interrupted[/]")
            raise
        except Exception as exc:
            report_exception(exc, context="interactive_shell.dispatch_async")
            console.print(f"[{ERROR}]dispatch error:[/] {escape(str(exc))}")
        finally:
            spinner.stop()
            # Release the per-turn cancel event only if it's still ours.
            # A stale-but-still-running prior-turn worker keeps a strong
            # reference to its own ``dispatch_cancel``; nothing else
            # holds a reference once we drop it here.
            if state.current_cancel_event is dispatch_cancel:
                state.current_cancel_event = None

    async def _processor() -> None:
        """Drain queued prompts one dispatch at a time."""
        while not state.exit_requested:
            try:
                text = await state.queue.get()
            except asyncio.CancelledError:
                return
            if state.exit_requested:
                state.queue.task_done()
                return
            state.current_task = asyncio.create_task(_run_one_dispatch(text))
            # ``try/except/pass`` (instead of ``contextlib.suppress``)
            # because CodeQL flags ``await x`` inside ``contextlib.suppress``
            # as "statement has no effect" even though the await IS the
            # effect. The ruff SIM105 suggestion is suppressed locally.
            try:  # noqa: SIM105
                await state.current_task
            except (asyncio.CancelledError, Exception):
                # Errors are already surfaced inside ``_run_one_dispatch``
                # (it prints ``· interrupted`` or the dispatch error).
                # Swallow here so the queue keeps draining the next item.
                pass
            state.current_task = None
            state.queue.task_done()

    def _message_with_spinner() -> ANSI:
        """Prompt message — spinner row above the top rule + ``❯`` prefix.

        The spinner row is *always* reserved: when streaming, the row
        shows ``⠋ thinking… (Ns · ↓ X tokens)``; when idle, it's a blank
        line. Reserving the row unconditionally is what keeps the
        input cursor from jumping up by one line when streaming starts
        and back down when it stops — Vaibhav's "still some jumping"
        was that vertical shift. Re-evaluated every
        ``refresh_interval`` tick so the spinner animates in place
        without redrawing the rule below it.
        """
        base = _prompt_message(session).value
        return ANSI(f"{spinner.inline_spinner_ansi()}\n{base}")

    processor_task = asyncio.create_task(_processor())
    try:
        with patch_stdout(raw=True):
            # ``erase_when_done=True`` on ``PromptSession`` clears the
            # input box the moment the user submits, so without an
            # explicit echo their question would never appear in
            # terminal scrollback. Echo it via a real ``Console``
            # (``patch_stdout`` routes the write above the active
            # prompt) so each turn looks like Claude Code:
            #
            #     [1] ❯ what is opensre?
            #     <response>
            #     · 5s · ↓ 100 tokens
            #
            #     [2] ❯ tell me more
            #
            # Constructed inside ``patch_stdout`` so Rich captures the
            # patched stdout proxy rather than the pre-patch
            # ``sys.stdout``.
            echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
            while True:
                # Hot-reload check (introduced in main) — picks up dev
                # edits to dispatch handlers between turns. No-op when
                # ``cfg.reload`` was off (hot_reloader is None).
                if hot_reloader is not None and not state.is_dispatch_running():
                    hot_reloader.check_and_reload(echo_console)
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=_PROMPT_REFRESH_INTERVAL_S,
                    )
                except EOFError:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    return
                except KeyboardInterrupt:
                    # Cancel the active turn first; if nothing's running,
                    # use main's two-press protocol — first Ctrl+C shows
                    # a hint, second exits.
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    if repl_prompt_note_ctrl_c(echo_console):
                        return
                    continue
                else:
                    repl_reset_ctrl_c_gate()

                if state.exit_requested:
                    return

                # If a worker thread is parked on a confirmation prompt,
                # the next text the user submits *might* be the answer
                # to that prompt — but only if it actually reads like a
                # y/n token. Type-ahead text the user submitted while
                # the action plan was still being parsed (e.g. a
                # follow-up question) used to get silently consumed as
                # the answer and decline the pending action; queue
                # those as a normal turn instead and leave the
                # confirmation parked for an explicit answer.
                if state.is_awaiting_confirmation():
                    if _looks_like_confirmation_answer(text):
                        state.deliver_confirmation(text or "")
                        continue
                    echo_console.print(
                        f"[{DIM}](type y/N to confirm the pending action; "
                        "your input has been queued for after)[/]"
                    )
                    stripped = (text or "").strip()
                    if stripped:
                        render_submitted_prompt(echo_console, session, stripped)
                        await state.queue.put(stripped)
                    continue

                stripped = (text or "").strip()
                if not stripped:
                    continue

                render_submitted_prompt(echo_console, session, stripped)
                await state.queue.put(stripped)
    finally:
        state.exit_requested = True
        state.cancel_current_dispatch()
        processor_task.cancel()
        # ``try/except/pass`` here (not ``contextlib.suppress``) so
        # CodeQL doesn't flag the bare ``await`` as ineffectual; SIM105
        # ruff suggestion is suppressed locally.
        try:  # noqa: SIM105
            await processor_task
        except (asyncio.CancelledError, Exception):
            # Processor cleanup must never raise — we're already in the
            # REPL's outer ``finally`` and the session is shutting down.
            # Suppress so the exit path completes cleanly.
            pass


def _route_confirm_through_prompt(state: _ReplState, prompt_text: str) -> str:
    """Worker-thread confirmation handler. Asks the user via the
    active prompt_toolkit input instead of stdlib ``input()``
    (which would deadlock against the running ``prompt_async``).

    Prints the confirmation prompt above the input, parks itself
    on a ``threading.Event``, and waits for the next text the user
    submits. Esc cancels and returns ``""`` (which execution_policy
    treats as "decline").

    Module-level (with explicit ``state``) rather than a closure inside
    :func:`_run_interactive` so the threaded happy-path / cancel-path
    tests can drive it directly.
    """
    sys.stdout.write(prompt_text)
    sys.stdout.flush()

    response_event = threading.Event()
    # Ordering matters: reset the response list BEFORE publishing
    # ``confirm_event``. ``deliver_confirmation`` early-exits when
    # ``confirm_event is None``, so the list reset is invisible to
    # the main thread; once ``confirm_event`` is non-None, any
    # concurrent ``deliver_confirmation`` appends to the fresh list
    # that this function then reads. Publishing the event first
    # would expose a window where the main thread appends to the
    # *previous* list (still referenced by ``state.confirm_response``)
    # and the next statement here would rebind to ``[]``, silently
    # dropping the user's answer.
    state.confirm_response = []
    state.confirm_event = response_event
    try:
        # Poll instead of wait-forever so cancel propagates within
        # one ``_PROMPT_REFRESH_INTERVAL_S`` tick. Poll the *active*
        # dispatch's cancel event (not a shared one) so a stale
        # cancel from a prior turn never shows up here.
        while not response_event.is_set():
            cancel = state.current_cancel_event
            if cancel is not None and cancel.is_set():
                return ""
            response_event.wait(timeout=_PROMPT_REFRESH_INTERVAL_S)
        return state.confirm_response[0] if state.confirm_response else ""
    finally:
        state.confirm_event = None
        state.confirm_response = []


def _build_cancel_key_bindings(state: _ReplState) -> KeyBindings:
    """Esc + Ctrl+L bindings — pulled out so the handlers can be reasoned
    about (and tested) independently of the prompt loop's coroutine
    machinery. ``state`` is the only mutable dependency; everything else
    is pure key-event handling.
    """
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        # Claude Code parity: Esc cancels the active stream when one is
        # running; otherwise it clears the input buffer (faster than
        # selecting + Backspacing a long typed prompt).
        if state.is_dispatch_running():
            state.cancel_current_dispatch()
            return
        if event.current_buffer.text:
            event.current_buffer.reset()

    @kb.add("c-l")
    def _on_ctrl_l(event: KeyPressEvent) -> None:
        # Clear the screen (terminal-native shortcut). The prompt
        # repaints automatically on the next render tick.
        event.app.renderer.clear()

    return kb


def _install_session_key_bindings(pt_session: object, extra_kb: KeyBindings) -> None:
    """Merge ``extra_kb`` into ``pt_session.key_bindings`` *before* the
    first ``prompt_async`` call. ``PromptSession`` caches the underlying
    ``Application`` on first use; ``prompt_async(key_bindings=...)``
    doesn't reliably invalidate that cache, so per-call overrides can
    be silently ignored. Mutating the session here ensures the cancel
    binding is baked into the cached app from the start.
    """
    existing = getattr(pt_session, "key_bindings", None)
    merged = merge_key_bindings([existing, extra_kb]) if existing is not None else extra_kb
    pt_session.key_bindings = merged  # type: ignore[attr-defined]


__all__ = ["run_repl"]
