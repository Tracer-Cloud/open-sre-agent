"""Textual ``App`` for the OpenSRE interactive REPL.

Layout (inline mode, top → bottom):

* :class:`textual.widgets.RichLog` — scrolling output area where each turn's
  prompt echo, rendered Markdown response, and status footer land.
  ``auto_scroll=True`` keeps the latest content visible. The widget has
  its own internal scrollbar (visually hidden via ``scrollbar-size: 0 0``)
  for navigating history within the widget. Native terminal scrollback
  for past conversation isn't currently supported in inline mode — the
  inline driver doesn't support ``App.suspend`` (``can_suspend == False``)
  and content written to the inline driver's stream creates ghost frames
  in scrollback. That's a follow-up requiring a custom driver or
  fullscreen mode (which is unreliable on macOS Terminal.app).
* :class:`StatusLine` — single-row reactive widget showing
  ``esc to interrupt`` while idle and
  ``⠋ thinking… (Ns · ↓ X tokens)  esc to interrupt`` while streaming.
* :class:`textual.widgets.Input` — input box pinned at the bottom of the
  inline app, remains editable (type-ahead) while a response streams.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

from app.cli.interactive_shell.session import ReplSession

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_CHARS_PER_TOKEN = 4


def _format_tokens(token_count: int) -> str:
    if token_count >= 1000:
        return f"{token_count / 1000:.1f}k"
    return str(token_count)


class StatusLine(Static):
    """Bottom-of-screen status: ``esc to interrupt`` or streaming indicator."""

    streaming: reactive[bool] = reactive(False)
    started_at: reactive[float] = reactive(0.0)
    bytes_in: reactive[int] = reactive(0)
    frame_idx: reactive[int] = reactive(0)

    def render(self) -> Text:
        if not self.streaming:
            return Text(
                "  esc to interrupt  ·  PgUp/PgDn to scroll log",
                style="dim",
            )
        elapsed = time.monotonic() - self.started_at
        tokens = _format_tokens(self.bytes_in // _CHARS_PER_TOKEN)
        glyph = _SPINNER_FRAMES[self.frame_idx % len(_SPINNER_FRAMES)]
        line = Text()
        line.append(f"  {glyph} thinking…", style="bold #B9EDAF")
        line.append(f" ({elapsed:.0f}s · ↓ {tokens} tokens)", style="dim")
        line.append("  esc to interrupt", style="dim")
        return line

    def tick(self) -> None:
        self.frame_idx += 1
        self.refresh()


class OpenSREApp(App):
    """Inline textual ``App``: RichLog + StatusLine + Input.

    ``dispatch_fn`` is the synchronous handler closure (from :mod:`loop`)
    that routes one submitted line through the same dispatch logic the
    pre-textual REPL used. We run it on a worker thread via
    :func:`asyncio.to_thread` so the textual loop keeps rendering and the
    input stays editable.
    """

    CSS = """
    Screen {
        height: auto;
    }
    Vertical {
        height: auto;
    }
    #log {
        height: auto;
        max-height: 60;
        padding: 0 1;
        scrollbar-size: 0 0;
    }
    #status {
        height: 1;
    }
    #input {
        height: 3;
        border: round green;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel active turn", show=False, priority=True),
        Binding("ctrl+c", "ctrl_c", "Cancel / Exit", show=False, priority=True),
        Binding("ctrl+d", "exit_repl", "Exit", show=False, priority=True),
        Binding("ctrl+q", "exit_repl", "Quit", show=False, priority=True),
        Binding("pageup", "log_scroll_up", "Scroll log up", show=False, priority=True),
        Binding("pagedown", "log_scroll_down", "Scroll log down", show=False, priority=True),
    ]

    def __init__(
        self,
        session: ReplSession,
        dispatch_fn: Callable[[str, OpenSREApp], None],
    ) -> None:
        super().__init__()
        self.session = session
        self.dispatch_fn = dispatch_fn
        self._active_task: asyncio.Task[Any] | None = None
        self._cancel_event: threading.Event | None = None
        self._ticker_task: asyncio.Task[Any] | None = None
        self._ctrl_c_count = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(
                id="log",
                wrap=True,
                markup=True,
                highlight=False,
                auto_scroll=True,
            )
            yield StatusLine(id="status")
            yield Input(
                id="input",
                placeholder="Type a message, /command, or paste an alert",
            )

    def on_mount(self) -> None:
        """Render the banner into the log, then focus the input.

        Banner rendering is deferred until after the first refresh so the
        ``RichLog`` widget has a measured width. We pre-render the banner
        to ANSI through a regular ``rich.console.Console`` (sized to the
        widget's width) and write each ANSI line back via
        ``Text.from_ansi`` — feeding ``Panel`` directly into ``RichLog``
        in inline mode produced a collapsed/empty box. Living inside the
        log means the banner scrolls together with response content
        (PageUp recovers it) instead of being frozen at the top of the
        terminal viewport.
        """
        self.call_after_refresh(self._write_banner_now)
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        self.query_one("#input", Input).focus()

    def _write_banner_now(self) -> None:
        from io import StringIO

        from rich.console import Console as RichConsole

        from app.cli.interactive_shell.banner import build_ready_panel

        log = self.log_widget
        width = max(40, log.size.width or 80)
        buf = StringIO()
        console = RichConsole(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=width,
            highlight=False,
        )
        console.print(build_ready_panel(session=self.session))
        for line in buf.getvalue().splitlines():
            log.write(Text.from_ansi(line))
        log.write(
            Text(
                "Type a message, /command, or paste an alert.  Ctrl+Q to quit.",
                style="dim",
            )
        )

    @property
    def log_widget(self) -> RichLog:
        return self.query_one("#log", RichLog)

    @property
    def status(self) -> StatusLine:
        return self.query_one("#status", StatusLine)

    @on(Input.Submitted, "#input")
    async def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # Always clear the input — the framework handles this via
        # ``input.value = ""`` which is non-racy in textual's reactive model.
        event.input.value = ""
        if not text:
            return
        # Cancel any in-flight turn AND wait for its ``finally`` block to
        # run before starting the new one. Without the await, the previous
        # turn's cleanup (``status.streaming = False``, ticker cancel) can
        # race against the new turn's setup (``status.streaming = True``,
        # new ticker), leaving streaming=False on the new turn — the
        # ticker's ``while self.status.streaming`` loop exits immediately
        # and the user sees no ``thinking…`` spinner.
        await self._cancel_active_turn_and_wait()
        # Echo the prompt above the streaming response.
        self.log_widget.write(Text(f"❯ {text}", style="bold #B9EDAF"))
        # Schedule the dispatch as a task. The worker thread runs the
        # synchronous handlers; textual keeps rendering meanwhile.
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._active_task = asyncio.create_task(self._run_dispatch(text, cancel_event))
        self._ctrl_c_count = 0

    async def _cancel_active_turn_and_wait(self) -> None:
        task = self._active_task
        if task is None or task.done():
            return
        task.cancel()
        if self._cancel_event is not None:
            self._cancel_event.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _run_dispatch(self, text: str, cancel_event: threading.Event) -> None:
        # Mark streaming + start the spinner ticker.
        status = self.status
        status.streaming = True
        status.started_at = time.monotonic()
        status.bytes_in = 0
        status.frame_idx = 0
        status.refresh()
        self._ticker_task = asyncio.create_task(self._tick_status())

        try:
            await asyncio.to_thread(self.dispatch_fn, text, self)
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        except Exception as exc:
            self.log_widget.write(
                Text(
                    f"✗ dispatch error: {type(exc).__name__}: {exc}",
                    style="bold #C45B52",
                )
            )
        finally:
            status.streaming = False
            status.refresh()
            if self._ticker_task is not None and not self._ticker_task.done():
                self._ticker_task.cancel()
            self._active_task = None

    async def _tick_status(self) -> None:
        try:
            while self.status.streaming:
                await asyncio.sleep(0.1)
                self.status.tick()
        except asyncio.CancelledError:
            return

    def _cancel_active_turn(self) -> None:
        if self._active_task is None or self._active_task.done():
            return
        self._active_task.cancel()
        if self._cancel_event is not None:
            self._cancel_event.set()

    def action_cancel(self) -> None:
        if self._active_task is None or self._active_task.done():
            return
        self._cancel_active_turn()
        self.log_widget.write(
            Text("· interrupting… (LLM stream finishes draining first)", style="dim")
        )

    def action_ctrl_c(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._cancel_active_turn()
            self._ctrl_c_count = 0
            return
        self._ctrl_c_count += 1
        if self._ctrl_c_count >= 2:
            self.exit()
        else:
            self.log_widget.write(Text("(Press Ctrl+C again to exit)", style="dim"))

    def action_exit_repl(self) -> None:
        self.exit()

    def action_log_scroll_up(self) -> None:
        """PageUp — scroll the log up one page without losing input focus."""
        self.log_widget.scroll_page_up(animate=False)

    def action_log_scroll_down(self) -> None:
        """PageDown — scroll the log down one page without losing input focus."""
        self.log_widget.scroll_page_down(animate=False)

    # ── Public API the dispatch closure uses to write into the log ────────

    def write_response(self, markdown_text: str) -> None:
        """Render an LLM response (Markdown) into the output log."""
        if not markdown_text:
            return
        self.log_widget.write(Markdown(markdown_text, code_theme="ansi_dark"))

    def write_status(self, text: str, *, dim: bool = True) -> None:
        """Append a small status line (e.g. ``· 9.4s · ↓ 333 tokens``)."""
        self.log_widget.write(Text(text, style="dim" if dim else ""))

    def write_error(self, text: str) -> None:
        """Append an error line in the project's ERROR colour."""
        self.log_widget.write(Text(text, style="bold #C45B52"))

    def write_plain(self, text: str) -> None:
        """Append a plain-text line (used by slash commands etc.)."""
        self.log_widget.write(text)

    def update_streaming_progress(self, bytes_received: int) -> None:
        """Called by the streaming layer to update the token counter."""
        self.status.bytes_in = bytes_received
        self.status.refresh()


def run_textual_repl(
    session: ReplSession,
    dispatch_fn: Callable[[str, OpenSREApp], None],
) -> int:
    """Synchronous entry point — runs the textual app to completion."""
    app = OpenSREApp(session, dispatch_fn)
    app.run()
    return 0


__all__ = ["OpenSREApp", "run_textual_repl"]
