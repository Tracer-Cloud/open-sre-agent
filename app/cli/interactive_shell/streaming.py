"""Live token streaming for interactive-shell LLM responses."""

from __future__ import annotations

import time
from collections.abc import Iterator

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from app.cli.interactive_shell.theme import BOLD_BRAND, DIM, HIGHLIGHT, MARKDOWN_THEME
from app.cli.support.prompt_support import CTRL_C_DOUBLE_PRESS_WINDOW_S

_SPINNER_NAME = "dots12"
_SPINNER_COLOR = HIGHLIGHT
_SPINNER_LABEL = "thinking"
_LIVE_REFRESH_PER_SECOND = 10
_STREAM_CANCEL_HINT = "Press Ctrl+C again to stop"

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


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
        with (
            console.use_theme(MARKDOWN_THEME),
            patch_stdout(raw=True),
            Live(
                spinner,
                console=console,
                refresh_per_second=_LIVE_REFRESH_PER_SECOND,
                transient=False,
                vertical_overflow="visible",
            ) as live,
        ):
            if buffer:
                live.update(Markdown("".join(buffer), code_theme="ansi_dark"))
            while True:
                chunk = _next_chunk(chunks_iter)
                if chunk is None:
                    break
                if not chunk:
                    continue
                buffer.append(chunk)
                live.update(Markdown("".join(buffer), code_theme="ansi_dark"))
            if not buffer:
                live.update(Text(""))
        if buffer:
            console.print(f"[{DIM}]· {time.monotonic() - started:.1f}s[/]")
    finally:
        console.print()

    return "".join(buffer)


__all__ = ["STREAM_LABEL_ANSWER", "STREAM_LABEL_ASSISTANT", "stream_to_console"]
