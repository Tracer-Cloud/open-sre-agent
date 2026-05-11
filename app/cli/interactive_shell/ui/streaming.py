"""Live token streaming for interactive-shell LLM responses."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from typing import Any

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from app.cli.interactive_shell.ui.theme import BOLD_BRAND, DIM, HIGHLIGHT, MARKDOWN_THEME
from app.cli.support.prompt_support import CTRL_C_DOUBLE_PRESS_WINDOW_S

if sys.platform == "win32":
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
else:

    class NoConsoleScreenBufferError(Exception):
        """Only the Windows prompt_toolkit stack raises this concrete type."""


_SPINNER_NAME = "dots12"
_SPINNER_COLOR = HIGHLIGHT
_SPINNER_LABEL = "thinking"
_LIVE_REFRESH_PER_SECOND = 10
# Cap how often we re-parse the accumulated buffer as Markdown. Without this,
# every incoming chunk triggers a full Markdown(buffer) parse, so a 10k-token
# response performs ~10k full re-parses of a growing string — O(n²) total work
# that visibly stalls long streams. Re-render at most refresh-rate times per
# second; final flush at end ensures the last chunks always land.
_LIVE_RENDER_INTERVAL_S = 1.0 / _LIVE_REFRESH_PER_SECOND
_STREAM_CANCEL_HINT = "Press Ctrl+C again to stop"

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


def _console_file_is_a_tty(console: Console) -> bool:
    """True only when Rich is writing to a real TTY (not StringIO / pytest capture).

    ``force_terminal=True`` sets ``is_terminal`` but does not provide a Windows
    console buffer; ``patch_stdout`` + ``Live`` then raise
    ``NoConsoleScreenBufferError`` on ``windows-latest``.
    """
    out = console.file
    isatty = getattr(out, "isatty", None)
    return bool(isatty and isatty())


def _run_throttled_markdown_loop(
    *,
    preview: Callable[[Any], None] | None = None,
    chunks_iter: Iterator[str],
    buffer: list[str],
    next_chunk: Callable[[Iterator[str]], str | None],
    # Backward-compat alias for long-lived REPL sessions where a hot reload
    # swapped this function under a caller that still binds the old keyword.
    # Once all callers have been refreshed (next REPL restart), this can go.
    set_view: Callable[[Any], None] | None = None,
    **_legacy_kwargs: Any,
) -> None:
    """Drain chunks into ``buffer`` and emit preview frames at most every
    ``_LIVE_RENDER_INTERVAL_S`` seconds.

    ``preview`` (legacy alias: ``set_view``) is called only with intermediate,
    possibly-cropped frames. It must NOT be relied on to render the final full
    response — callers are responsible for printing the final Markdown once
    after this loop returns, so that the visible result is one static block
    rather than a stack of re-renders that accumulate in terminal scrollback.

    Unknown keyword arguments are absorbed silently so a hot-reloaded module
    whose call site was edited can never crash a long-lived shell with a
    ``TypeError: unexpected keyword argument`` — the user just sees the new
    signature's behaviour next turn.
    """
    renderer = preview or set_view
    if renderer is None:
        raise ValueError("stream preview callback is required")

    last_render = 0.0
    if buffer:
        renderer(Markdown("".join(buffer), code_theme="ansi_dark"))
        last_render = time.monotonic()
    while True:
        chunk = next_chunk(chunks_iter)
        if chunk is None:
            break
        if not chunk:
            continue
        buffer.append(chunk)
        now = time.monotonic()
        if now - last_render >= _LIVE_RENDER_INTERVAL_S:
            renderer(Markdown("".join(buffer), code_theme="ansi_dark"))
            last_render = now


def stream_to_console(
    console: Console,
    *,
    label: str,
    chunks: Iterator[str],
    suppress_if_starts_with: str | None = None,
) -> str:
    """Render a streaming LLM response live and return the accumulated text.

    Uses patch_stdout so prompt_toolkit keeps the input frame rendered at the
    bottom of the terminal while output streams above it.

    Works when ``console.file`` is a real TTY; ``StringIO`` / CI capture and
    Windows environments without a console screen buffer fall back to the same
    throttle + Markdown rendering via plain prints (no ``Live`` / raw console).

    ``suppress_if_starts_with`` allows callers to skip live rendering when the
    initial non-whitespace token indicates machine-readable payloads (for
    example JSON action plans).
    """
    if not console.is_terminal:
        text = "".join(chunks)
        if suppress_if_starts_with is not None and text.lstrip().startswith(
            suppress_if_starts_with
        ):
            return text
        if text:
            console.print()
            console.print(f"[{BOLD_BRAND}]{label}:[/]")
            with console.use_theme(MARKDOWN_THEME):
                console.print(Markdown(text, code_theme="ansi_dark"))
            console.print()
        return text

    chunks_iter = iter(chunks)
    peeked: list[str] = []
    first_interrupt_at: float | None = None

    def _note_stream_interrupt() -> None:
        nonlocal first_interrupt_at
        now = time.monotonic()
        if (
            first_interrupt_at is not None
            and now - first_interrupt_at <= CTRL_C_DOUBLE_PRESS_WINDOW_S
        ):
            first_interrupt_at = None
            raise KeyboardInterrupt
        first_interrupt_at = now
        console.print(f"[{DIM}]{_STREAM_CANCEL_HINT}[/]")

    def _next_chunk(it: Iterator[str]) -> str | None:
        while True:
            try:
                return next(it)
            except StopIteration:
                return None
            except KeyboardInterrupt:
                _note_stream_interrupt()

    if suppress_if_starts_with is not None:
        while True:
            chunk = _next_chunk(chunks_iter)
            if chunk is None:
                break
            peeked.append(chunk)
            stripped = "".join(peeked).lstrip()
            if not stripped:
                continue
            if stripped.startswith(suppress_if_starts_with):
                drained: list[str] = []
                while True:
                    rest = _next_chunk(chunks_iter)
                    if rest is None:
                        break
                    drained.append(rest)
                return "".join(peeked) + "".join(drained)
            break

    buffer: list[str] = list(peeked)
    spinner = Spinner(
        _SPINNER_NAME,
        text=Text(f"{_SPINNER_LABEL}…", style=f"bold {_SPINNER_COLOR}"),
        style=f"bold {_SPINNER_COLOR}",
    )

    console.print()
    console.print(f"[{BOLD_BRAND}]{label}:[/]")

    started = time.monotonic()
    try:
        with console.use_theme(MARKDOWN_THEME):

            def _noop_preview(_renderable: Any) -> None:
                """Used when Live is unavailable.

                Per-tick rendering would print the entire cumulative buffer to
                stdout on each refresh, producing a staircase of duplicated
                content in scrollback. We skip in-flight previews and rely on
                the post-loop final print below to render the response once.
                """

            def _live_kwargs() -> dict[str, Any]:
                return {
                    "console": console,
                    "refresh_per_second": _LIVE_REFRESH_PER_SECOND,
                    # Clear the live frame on exit so the final static print
                    # below is the single canonical rendering of the response.
                    "transient": True,
                    # Crop the in-flight preview to the visible region. Without
                    # this, content taller than the terminal pushes each
                    # refresh frame into scrollback as a permanent artifact.
                    "vertical_overflow": "ellipsis",
                }

            def _run_throttled_with_live(*, wrap_patch_stdout: bool) -> None:
                if wrap_patch_stdout:
                    with (
                        patch_stdout(raw=True),
                        Live(spinner, **_live_kwargs()) as live_ref,
                    ):
                        _run_throttled_markdown_loop(
                            preview=live_ref.update,
                            chunks_iter=chunks_iter,
                            buffer=buffer,
                            next_chunk=_next_chunk,
                        )
                else:
                    with Live(spinner, **_live_kwargs()) as live_ref:
                        _run_throttled_markdown_loop(
                            preview=live_ref.update,
                            chunks_iter=chunks_iter,
                            buffer=buffer,
                            next_chunk=_next_chunk,
                        )

            # Streaming may raise (upstream HTTP error, double Ctrl+C, etc.).
            # In every case we want the partial buffer rendered exactly once
            # as a static block so the user — and any error label printed by
            # the caller — sees what was produced before the failure.
            try:
                is_real_tty = _console_file_is_a_tty(console)
                if is_real_tty:
                    try:
                        _run_throttled_with_live(wrap_patch_stdout=True)
                    except NoConsoleScreenBufferError:
                        try:
                            _run_throttled_with_live(wrap_patch_stdout=False)
                        except NoConsoleScreenBufferError:
                            _run_throttled_markdown_loop(
                                preview=_noop_preview,
                                chunks_iter=chunks_iter,
                                buffer=buffer,
                                next_chunk=_next_chunk,
                            )
                else:
                    # force_terminal=True but backed by a non-TTY file (StringIO
                    # in tests, piped output): skip Live to avoid cursor-positioning
                    # sequences writing raw intermediate markdown into the buffer.
                    _run_throttled_markdown_loop(
                        preview=_noop_preview,
                        chunks_iter=chunks_iter,
                        buffer=buffer,
                        next_chunk=_next_chunk,
                    )
            finally:
                # Single authoritative render. The live preview was transient
                # + cropped while streaming; this is what ends up in
                # scrollback. Runs on both success and exception so callers
                # can surface error context below the partial response.
                if buffer:
                    console.print(Markdown("".join(buffer), code_theme="ansi_dark"))
                else:
                    console.print(Text(""))
        if buffer:
            console.print(f"[{DIM}]· {time.monotonic() - started:.1f}s[/]")
    finally:
        console.print()

    return "".join(buffer)


__all__ = ["STREAM_LABEL_ANSWER", "STREAM_LABEL_ASSISTANT", "stream_to_console"]
