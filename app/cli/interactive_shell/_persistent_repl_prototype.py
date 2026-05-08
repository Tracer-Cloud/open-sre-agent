"""Prototype: persistent prompt_toolkit Application with bottom-pinned input.

This file proves the architectural pattern for #1679 before we touch any
production code. It demonstrates:

  1. Input frame stays pinned at the bottom of the terminal at all times.
  2. The user can type the next prompt while the previous one is still
     streaming (type-ahead).
  3. Esc cancels the active stream cleanly without leaving the REPL.
  4. Output flows into the area between the previous prompt and the input
     frame (history accumulates above; auto-scrolls).

When the prototype is validated, the real REPL (``loop.py``) will be
migrated to this shape and this file gets deleted.

Run interactively::

    python -m app.cli.interactive_shell._persistent_repl_prototype

Type a message and press Enter. A simulated streamer yields characters at
~30 cps; while it runs, the input field is still editable. Press Esc to
cancel mid-stream. Press Ctrl+C twice to exit.

Notes for the migration phase:

* The fake streamer (``_fake_stream``) is a stand-in for the real
  ``client.invoke_stream(prompt)`` async iterator we already have.
* The history buffer here is plain text; the production migration will
  render Markdown into ``FormattedText`` (separate concern, evaluated
  after the architectural pattern is validated).
* Throttle logic from #1649 isn't reproduced here — it lives in
  ``streaming.py`` and will be reused in the migrated path.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.styles import Style

# Spinner frames + tick interval used by the status bar.
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_SPINNER_TICK_S = 0.1


class _History:
    """Accumulating output above the input frame.

    Stores ``(style_class, text)`` tuples. The ``Window`` rendering this
    area auto-scrolls to follow new content because we always append.
    """

    def __init__(self) -> None:
        self._segments: list[tuple[str, str]] = [
            (
                "class:hint",
                "type a message + Enter to submit · Esc to cancel · Ctrl+C twice to exit\n\n",
            ),
        ]
        self._streaming_segment_index: int | None = None

    def get_text(self) -> FormattedText:
        return FormattedText(self._segments)

    def add_user_prompt(self, text: str) -> None:
        self._segments.append(("class:prompt", f"› {text}\n"))
        self._streaming_segment_index = None

    def begin_response(self, label: str) -> None:
        # Reserve a segment for the streaming response so subsequent
        # ``append_chunk`` calls can extend it in place.
        self._segments.append(("class:label", f"\n{label}: "))
        self._segments.append(("class:assistant", ""))
        self._streaming_segment_index = len(self._segments) - 1

    def append_chunk(self, chunk: str) -> None:
        if self._streaming_segment_index is None:
            return
        style, text = self._segments[self._streaming_segment_index]
        self._segments[self._streaming_segment_index] = (style, text + chunk)

    def finalize_response(self, elapsed: float) -> None:
        self._segments.append(("class:dim", f"\n· {elapsed:.1f}s\n"))
        self._streaming_segment_index = None

    def add_cancelled(self) -> None:
        self._segments.append(("class:dim", "\n· cancelled\n"))
        self._streaming_segment_index = None


class _StatusBar:
    """One-line row between history and input.

    Shows a thinking spinner + cancel hint while streaming; submit hint
    otherwise.
    """

    def __init__(self) -> None:
        self.streaming: bool = False
        self._spinner_idx: int = 0

    def get_text(self) -> FormattedText:
        if self.streaming:
            frame = _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]
            return FormattedText(
                [
                    ("class:spinner", f" {frame} thinking… "),
                    ("class:dim", "  Esc to cancel"),
                ]
            )
        return FormattedText([("class:dim", " ↵ submit · Ctrl+C twice to exit ")])

    def tick(self) -> None:
        self._spinner_idx += 1


async def _fake_stream(prompt: str) -> AsyncIterator[str]:
    """Stand-in for ``client.invoke_stream(prompt)``.

    Yields a long-ish response one character at a time so the prototype
    visibly demonstrates streaming behavior. Throws in some pauses so the
    user has time to test type-ahead and cancellation.
    """
    response = (
        f"Got it — you said: {prompt!r}.\n"
        "This is a simulated response that arrives one character at a time, "
        "so you can see the streaming behavior. Try typing the next prompt "
        "while this one is still streaming — the input field below should "
        "stay editable. Press Esc to cancel mid-stream.\n\n"
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut "
        "enim ad minim veniam, quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat.\n"
    )
    for ch in response:
        await asyncio.sleep(1 / 30)
        yield ch


async def main() -> None:
    history = _History()
    status = _StatusBar()
    # ``InMemoryHistory`` lets Up/Down arrows recall previously-submitted
    # prompts. Persists for the lifetime of the Application; doesn't write
    # to disk (production migration will hook into the existing
    # ``app/cli/interactive_shell/history.py`` file-backed store).
    prompt_history = InMemoryHistory()
    input_buffer = Buffer(multiline=False, history=prompt_history)

    # State held in the closure so key handlers and the streaming task
    # can share it. ``active_task`` is ``None`` when nothing is streaming.
    active_task: asyncio.Task[Any] | None = None

    layout = Layout(
        HSplit(
            [
                # History area — fills available vertical space, scrolls with content.
                Window(
                    content=FormattedTextControl(text=history.get_text, focusable=False),
                    wrap_lines=True,
                ),
                # Status bar — exactly one row.
                Window(
                    content=FormattedTextControl(text=status.get_text, focusable=False),
                    height=D.exact(1),
                ),
                # Input field — exactly one row at the bottom.
                Window(
                    content=BufferControl(buffer=input_buffer),
                    height=D.exact(1),
                ),
            ]
        ),
        focused_element=input_buffer,
    )

    style = Style.from_dict(
        {
            "hint": "#888",
            "prompt": "ansibrightcyan bold",
            "label": "ansibrightyellow bold",
            "assistant": "",
            "dim": "#666",
            "spinner": "ansibrightyellow bold",
        }
    )

    kb = KeyBindings()
    ctrl_c_count = [0]

    # The Application reference is captured by the streaming + spinner-tick
    # tasks for ``invalidate()`` calls. Set after construction below.
    app: Application[Any] | None = None

    async def run_stream(prompt: str) -> None:
        """One streaming turn — used as the body of an ``asyncio.Task``."""
        nonlocal active_task
        status.streaming = True
        history.begin_response("assistant")
        if app is not None:
            app.invalidate()
        started = time.monotonic()
        try:
            async for chunk in _fake_stream(prompt):
                history.append_chunk(chunk)
                if app is not None:
                    app.invalidate()
        except asyncio.CancelledError:
            history.add_cancelled()
            if app is not None:
                app.invalidate()
            raise
        else:
            history.finalize_response(time.monotonic() - started)
        finally:
            status.streaming = False
            active_task = None
            if app is not None:
                app.invalidate()

    @kb.add("enter")
    def _on_submit(_event: Any) -> None:
        nonlocal active_task
        text = input_buffer.text.strip()
        input_buffer.text = ""
        if not text:
            return
        # Record for Up/Down arrow recall.
        prompt_history.append_string(text)
        history.add_user_prompt(text)
        # If a previous stream is still running, queue this as the next
        # turn after it completes. (For prototype simplicity we just
        # cancel the prior task; production might wait for it.)
        if active_task is not None and not active_task.done():
            active_task.cancel()
        active_task = asyncio.create_task(run_stream(text))
        ctrl_c_count[0] = 0  # any input resets the exit-on-second-ctrl-c counter

    # ``eager=True`` fires this binding immediately on Esc instead of
    # waiting ~500ms for prompt_toolkit to resolve possible escape
    # sequences (arrow keys etc. all start with ESC). Without it, Esc
    # feels laggy — which is exactly what you reported in the first run.
    @kb.add("escape", eager=True)
    def _on_esc(_event: Any) -> None:
        nonlocal active_task
        if active_task is not None and not active_task.done():
            active_task.cancel()

    @kb.add("c-c")
    def _on_ctrl_c(event: Any) -> None:
        nonlocal active_task
        # First press while streaming = cancel; otherwise increments exit counter.
        if active_task is not None and not active_task.done():
            active_task.cancel()
            return
        ctrl_c_count[0] += 1
        if ctrl_c_count[0] >= 2:
            event.app.exit()

    # ``full_screen=True`` is what gives us the Claude-CLI layout: the
    # terminal switches to its alt-screen buffer, the History / Status /
    # Input windows render in fixed regions, and new content goes into
    # the right region without interleaving with other output. In
    # ``full_screen=False`` mode the windows render inline with the
    # terminal flow, so submitting a new prompt mid-stream glues the new
    # ``› ...`` prefix onto the trailing chunk of the running response.
    app = Application(
        layout=layout,
        # Merge with prompt_toolkit's default bindings so Left/Right/Home/End/
        # Backspace work inside the input field, and Up/Down recall entries
        # from ``prompt_history``. Without this merge, only the custom keys
        # below are active and the prompt isn't steerable.
        key_bindings=merge_key_bindings([load_key_bindings(), kb]),
        style=style,
        full_screen=True,
        mouse_support=False,
    )

    async def spinner_refresh() -> None:
        # Background tick so the spinner glyph animates while streaming.
        # Without this, the layout only re-renders on chunk arrival.
        while True:
            await asyncio.sleep(_SPINNER_TICK_S)
            if status.streaming:
                status.tick()
                if app is not None:
                    app.invalidate()

    refresh_task = asyncio.create_task(spinner_refresh())
    try:
        await app.run_async()
    finally:
        refresh_task.cancel()
        if active_task is not None and not active_task.done():
            active_task.cancel()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        asyncio.run(main())
