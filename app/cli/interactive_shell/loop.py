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

Earlier iterations of #1679 used a textual-based persistent app with an
inline ``RichLog``. That fought with macOS Terminal.app's native scroll
(content stayed inside a widget instead of going to terminal scrollback)
and with the user's expectation of a single, native scroll axis. The
``prompt_toolkit`` + ``patch_stdout`` shape — which is how Claude Code
behaves — gives up the declarative widget tree but matches terminal
conventions: input pinned at bottom, history scrolls naturally.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from app.agents.sweep import run_startup_sweep
from app.analytics.cli import capture_terminal_turn_summarized
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.agent_actions import execute_cli_actions_with_metrics
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up
from app.cli.interactive_shell.prompt_surface import (
    _build_prompt_session,
    _prompt_message,
    _prompt_rule_ansi,
    render_submitted_prompt,
)
from app.cli.interactive_shell.router import route_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.streaming import format_token_count_short
from app.cli.interactive_shell.theme import (
    ANSI_DIM,
    ANSI_RESET,
    DIM,
    ERROR,
    PROMPT_ACCENT_ANSI,
    WARNING,
)
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception

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
    from app.cli.interactive_shell.execution_policy import (
        evaluate_investigation_launch,
        execution_allowed,
    )
    from app.cli.interactive_shell.tasks import TaskKind
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

    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
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
        cmd_text = text if text.startswith("/") else f"/{text}"
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
        answer_cli_agent(text, session, console, confirm_fn=confirm_fn)
        session.record("cli_agent", text)
        return

    if kind == "new_alert":
        _run_new_alert(text, session, console, confirm_fn=confirm_fn)
        return

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console)
    session.record("follow_up", text)


def _run_initial_input(initial_input: str, session: ReplSession) -> int:
    """Test-harness path — drain pre-seeded input through the same dispatch logic."""
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    exit_requested = [False]

    def _early_exit() -> None:
        exit_requested[0] = True

    for line in initial_input.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        _dispatch_one_turn(stripped, session, console, on_exit=_early_exit)
        if exit_requested[0]:
            return 0
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
    session = ReplSession()

    if initial_input:
        try:
            return _run_initial_input(initial_input, session)
        except (EOFError, KeyboardInterrupt):
            return 0

    # Banner prints to real stdout and lives in the user's terminal
    # scrollback above all subsequent turns — same place as past responses,
    # so native terminal scroll reveals everything from the session top
    # downward, no widget-internal scroll axis.
    real_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(real_console)

    with contextlib.suppress(EOFError, KeyboardInterrupt):
        asyncio.run(_run_interactive(session))
    return 0


# How often the prompt's ``bottom_toolbar`` and ``message`` callables
# re-evaluate. 100 ms paces the spinner glyph animation and the token
# counter without burning CPU on the prompt-toolkit render loop.
_PROMPT_REFRESH_INTERVAL_S = 0.1


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
    # ``threading.Event`` because the dispatch runs in a worker thread
    # via ``asyncio.to_thread`` — ``task.cancel()`` alone doesn't reach
    # the worker. Streaming.py polls ``console.cancel_requested``
    # between chunks; setting this event here breaks that loop.
    cancel_event: threading.Event = field(default_factory=threading.Event)
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

        ``cancel_event`` is what stops the streaming loop in
        :func:`stream_to_console` (worker thread); ``Task.cancel()`` is
        what unblocks the asyncio waiter in :meth:`_run_one_dispatch`
        (main thread). Both are needed. Also unparks any worker thread
        waiting on a confirmation prompt so it doesn't hang on Esc.
        """
        self.cancel_event.set()
        if self.confirm_event is not None:
            self.confirm_event.set()
        if self.current_task is not None and not self.current_task.done():
            self.current_task.cancel()


class _SpinnerState:
    """Mutable state read by the prompt's bottom-toolbar callback.

    The toolbar callback runs every ``refresh_interval`` (~100 ms) while a
    turn streams; it reads ``streaming``, ``started_at``, ``bytes_in`` to
    compose the live ``⠋ thinking… (Ns · ↓ X tokens)`` line. Streaming
    layer (:mod:`streaming`) updates ``bytes_in`` via a console hook —
    see :class:`_StreamingConsole` below.
    """

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _CHARS_PER_TOKEN = 4
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
        """Bottom toolbar: rule + state-aware hint. The animated spinner
        lives in :meth:`inline_spinner_ansi`, which is prepended to the
        prompt ``message`` so it appears *above* the input frame — at
        the end of the last response chunk — matching Claude Code's
        layout.

        Hint text follows Claude Code: ``esc to interrupt`` while a turn
        is streaming, ``/ for commands  ·  ↑↓ history  ·  esc to clear``
        when idle so the user sees what each key does.
        """
        rule = _prompt_rule_ansi()
        if self.streaming:
            hint = "esc to interrupt"
        else:
            hint = "/ for commands  ·  ↑↓ history  ·  esc to clear"
        return ANSI(f"{rule}\n{ANSI_DIM}  {hint}{ANSI_RESET}")

    def inline_spinner_ansi(self) -> str:
        """Single-line ``⠋ thinking… (Ns · ↓ X tokens)`` indicator, or
        empty string when not streaming. Rendered above the input rule
        so it sits at the visual end of the response stream.
        """
        if not self.streaming:
            return ""
        elapsed = time.monotonic() - self.started_at
        tokens_str = format_token_count_short(self.bytes_in // self._CHARS_PER_TOKEN)
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
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
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


async def _run_interactive(session: ReplSession) -> None:
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
    """
    pt_session = _build_prompt_session(session)
    spinner = _SpinnerState()
    state = _ReplState()

    cancel_kb = _build_cancel_key_bindings(state)
    _install_session_key_bindings(pt_session, cancel_kb)

    def _request_exit() -> None:
        state.exit_requested = True
        state.cancel_current_dispatch()
        app = get_app_or_none()
        if app is not None:
            app.exit()

    def _route_confirm_through_prompt(prompt_text: str) -> str:
        """Worker-thread confirmation handler. Asks the user via the
        active prompt_toolkit input instead of stdlib ``input()``
        (which would deadlock against the running ``prompt_async``).

        Prints the confirmation prompt above the input, parks itself
        on a ``threading.Event``, and waits for the next text the user
        submits. Esc cancels and returns ``""`` (which execution_policy
        treats as "decline").
        """
        sys.stdout.write(prompt_text)
        sys.stdout.flush()

        response_event = threading.Event()
        state.confirm_event = response_event
        state.confirm_response = []
        try:
            # Poll instead of wait-forever so cancel propagates within
            # one ``_PROMPT_REFRESH_INTERVAL_S`` tick.
            while not response_event.is_set():
                if state.cancel_event.is_set():
                    return ""
                response_event.wait(timeout=_PROMPT_REFRESH_INTERVAL_S)
            return state.confirm_response[0] if state.confirm_response else ""
        finally:
            state.confirm_event = None
            state.confirm_response = []

    async def _run_one_dispatch(text: str) -> None:
        # Reset the cancel event for this turn — Esc on a previous turn
        # would otherwise keep this one from running at all.
        state.cancel_event.clear()
        console = _StreamingConsole(
            spinner,
            state.cancel_event,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
        )
        spinner.start()
        try:
            await asyncio.to_thread(
                _dispatch_one_turn,
                text,
                session,
                console,
                on_exit=_request_exit,
                confirm_fn=_route_confirm_through_prompt,
            )
        except asyncio.CancelledError:
            console.print(f"[{WARNING}]· interrupted[/]")
            raise
        except Exception as exc:
            report_exception(exc, context="interactive_shell.dispatch_async")
            console.print(f"[{ERROR}]dispatch error:[/] {escape(str(exc))}")
        finally:
            spinner.stop()

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
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await state.current_task
            state.current_task = None
            state.queue.task_done()

    def _message_with_spinner() -> ANSI:
        """Prompt message — spinner line (when streaming) above the
        input rule + ``❯`` prefix. The callable is re-evaluated every
        ``refresh_interval`` tick so the spinner glyph and token
        counter animate in place.
        """
        base = _prompt_message(session).value
        spinner_part = spinner.inline_spinner_ansi()
        if spinner_part:
            return ANSI(f"{spinner_part}\n{base}")
        return ANSI(base)

    # ``erase_when_done=True`` on ``PromptSession`` clears the input box
    # the moment the user submits, so without an explicit echo their
    # question would never appear in terminal scrollback. Echo it via a
    # real ``Console`` (``patch_stdout`` routes the write above the
    # active prompt) so each turn looks like Claude Code:
    #
    #     [1] ❯ what is opensre?
    #     <response>
    #     · 5s · ↓ 100 tokens
    #
    #     [2] ❯ tell me more
    #     ...
    echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")

    processor_task = asyncio.create_task(_processor())
    try:
        with patch_stdout(raw=True):
            while True:
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=_PROMPT_REFRESH_INTERVAL_S,
                    )
                except (EOFError, KeyboardInterrupt):
                    # Ctrl+C / Ctrl+D cancels the currently-running
                    # turn if one is active; otherwise exits the REPL.
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    return

                if state.exit_requested:
                    return

                # If a worker thread is parked on a confirmation prompt,
                # the next text the user submits is the *answer* to that
                # prompt, not a new turn. Deliver it and resume; do NOT
                # echo it as a turn or enqueue it.
                if state.is_awaiting_confirmation():
                    state.deliver_confirmation(text or "")
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
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await processor_task


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
