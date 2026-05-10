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
import re
import sys
import threading
import time
from collections.abc import Callable

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
from app.cli.interactive_shell.theme import (
    ANSI_DIM,
    ANSI_RESET,
    DIM,
    ERROR,
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
) -> None:
    """Route + dispatch one accepted line. Pure synchronous body.

    Used both from :class:`PersistentRepl` (wrapped in ``asyncio.to_thread``)
    and from the ``initial_input`` pre-seeding path. ``on_exit`` is called
    when a slash command requests REPL exit (e.g. ``/exit``); the caller
    decides what that means (in the persistent path, ``app.exit()``; in the
    pre-seeded path, an early return).
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
        turn = execute_cli_actions_with_metrics(text, session, console)
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
        answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        return

    if kind == "new_alert":
        _run_new_alert(text, session, console)
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

    def __init__(self) -> None:
        self.streaming: bool = False
        self.started_at: float = 0.0
        self.bytes_in: int = 0
        self._frame_idx: int = 0

    def start(self) -> None:
        self.streaming = True
        self.started_at = time.monotonic()
        self.bytes_in = 0
        self._frame_idx = 0

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
        tokens = self.bytes_in // self._CHARS_PER_TOKEN
        if tokens >= 1000:
            tokens_str = f"{tokens / 1000:.1f}k"
        else:
            tokens_str = str(tokens)
        glyph = self._SPINNER_FRAMES[self._frame_idx % len(self._SPINNER_FRAMES)]
        self._frame_idx += 1
        return (
            f"\x1b[1;38;2;185;237;175m{glyph} thinking…{ANSI_RESET}"
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

    queue: asyncio.Queue[str] = asyncio.Queue()
    current_dispatch: dict[str, asyncio.Task[None] | None] = {"task": None}
    exit_requested = {"flag": False}
    # ``threading.Event`` because the dispatch runs in a worker thread
    # via ``asyncio.to_thread`` — ``task.cancel()`` alone doesn't reach
    # the worker. Streaming.py polls ``console.cancel_requested``
    # between chunks; setting this event here breaks that loop.
    cancel_event = threading.Event()

    def _cancel_current() -> None:
        cancel_event.set()
        task = current_dispatch["task"]
        if task is not None and not task.done():
            task.cancel()

    cancel_kb = KeyBindings()

    @cancel_kb.add("escape", eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        # Claude Code parity: Esc cancels the active stream when one is
        # running; otherwise it clears the input buffer (faster than
        # selecting + Backspacing a long typed prompt).
        task = current_dispatch["task"]
        if task is not None and not task.done():
            _cancel_current()
            return
        if event.current_buffer.text:
            event.current_buffer.reset()

    @cancel_kb.add("c-l")
    def _on_ctrl_l(event: KeyPressEvent) -> None:
        # Clear the screen (terminal-native shortcut). The prompt
        # repaints automatically on the next render tick.
        event.app.renderer.clear()

    # Mutate the session's bindings BEFORE any ``prompt_async`` call —
    # ``PromptSession`` caches the underlying ``Application`` on first
    # use and ``prompt_async(key_bindings=...)`` doesn't reliably
    # invalidate that cache, so per-call overrides can be silently
    # ignored. Setting ``pt_session.key_bindings`` upfront ensures the
    # cancel binding is baked into the cached app from the start.
    existing_kb = pt_session.key_bindings
    pt_session.key_bindings = (
        merge_key_bindings([existing_kb, cancel_kb]) if existing_kb is not None else cancel_kb
    )

    def _request_exit() -> None:
        exit_requested["flag"] = True
        _cancel_current()
        app = get_app_or_none()
        if app is not None:
            app.exit()

    async def _run_one_dispatch(text: str) -> None:
        # Reset the cancel event for this turn — Esc on a previous turn
        # would otherwise keep this one from running at all.
        cancel_event.clear()
        console = _StreamingConsole(
            spinner,
            cancel_event,
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
        while not exit_requested["flag"]:
            try:
                text = await queue.get()
            except asyncio.CancelledError:
                return
            if exit_requested["flag"]:
                queue.task_done()
                return
            task = asyncio.create_task(_run_one_dispatch(text))
            current_dispatch["task"] = task
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            current_dispatch["task"] = None
            queue.task_done()

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

    processor_task = asyncio.create_task(_processor())
    try:
        with patch_stdout(raw=True):
            while True:
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=0.1,
                    )
                except (EOFError, KeyboardInterrupt):
                    # Ctrl+C / Ctrl+D cancels the currently-running
                    # turn if one is active; otherwise exits the REPL.
                    if current_dispatch["task"] is not None and not current_dispatch["task"].done():
                        _cancel_current()
                        continue
                    return

                if exit_requested["flag"]:
                    return

                stripped = (text or "").strip()
                if not stripped:
                    continue

                await queue.put(stripped)
    finally:
        exit_requested["flag"] = True
        _cancel_current()
        processor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await processor_task


__all__ = ["run_repl"]
