"""Live token streaming for interactive-shell LLM responses.

Drives a Rich.Live region while the model emits text chunks, re-rendering the
buffer as Markdown each chunk so formatting (bold, lists, code spans) appears
progressively — same feel as Claude CLI. Falls back to a drain-and-return on
non-terminal consoles so captured logs stay clean.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD

# Match loaders.py spinner so streaming and non-streaming surfaces look
# identical. Duplicated rather than imported because loaders.py keeps these
# constants file-local by convention.
_SPINNER_NAME = "dots12"
_SPINNER_COLOR = "orange1"
_SPINNER_LABEL = "thinking"

# Rich.Live redraw rate. 20fps gives a Claude-CLI-like smoothness during
# token streaming — the default 10fps reads as visibly choppy on long
# answers, while going much higher just burns CPU re-parsing Markdown
# without a perceptible UX gain.
_LIVE_REFRESH_PER_SECOND = 20

# Header labels printed above streamed responses. Centralized so the four
# call sites (cli_help / cli_agent / follow_up + the action-card renderer)
# stay in sync if the brand naming ever changes.
STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


def stream_to_console(
    console: Console,
    *,
    label: str,
    chunks: Iterator[str],
    suppress_if_starts_with: str | None = None,
) -> str:
    """Render a streaming LLM response live, return the accumulated text.

    On a terminal console, prints the ``label`` header, then opens a
    ``rich.live.Live`` region that starts on a "thinking…" spinner and
    swaps to a Markdown render of the accumulated buffer once chunks
    start arriving. Each chunk re-renders the buffer in place — the user
    sees text appear progressively with formatting (bold, lists, code
    spans) applied live. The final content stays visible on screen; no
    clear-and-reprint at finalize.

    On a non-terminal console (CI, captured stdout, piped output) the
    stream is drained silently and returned as a single string. No Live
    region, no spinner artifacts in captured logs.

    If the stream raises mid-flight, whatever was accumulated remains on
    screen as the exception propagates so the caller can surface an
    error label below the partial response.

    ``suppress_if_starts_with`` peeks the first non-whitespace character
    of the stream. If it matches, the stream is drained silently and the
    full text is returned without rendering — useful when the response
    might be a JSON action plan that should not leak to the UI. The
    caller decides what to do with the buffered text.
    """
    if not console.is_terminal:
        text = "".join(chunks)
        if suppress_if_starts_with is not None and text.lstrip().startswith(
            suppress_if_starts_with
        ):
            # Caller suppresses raw payload (e.g. JSON action plan); return only.
            return text
        if text:
            console.print()
            console.print(f"[{TERMINAL_ACCENT_BOLD}]{label}:[/]")
            console.print(Markdown(text))
            console.print()
        return text

    chunks_iter = iter(chunks)
    peeked: list[str] = []

    if suppress_if_starts_with is not None:
        for chunk in chunks_iter:
            peeked.append(chunk)
            stripped = "".join(peeked).lstrip()
            if not stripped:
                continue
            if stripped.startswith(suppress_if_starts_with):
                # Suppressed: drain remaining silently and return.
                return "".join(peeked) + "".join(chunks_iter)
            break

    buffer: list[str] = list(peeked)
    spinner = Spinner(
        _SPINNER_NAME,
        text=Text(f"{_SPINNER_LABEL}…", style=f"bold {_SPINNER_COLOR}"),
        style=f"bold {_SPINNER_COLOR}",
    )

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]{label}:[/]")

    started = time.monotonic()
    try:
        with Live(
            spinner,
            console=console,
            refresh_per_second=_LIVE_REFRESH_PER_SECOND,
            transient=False,
        ) as live:
            if buffer:
                live.update(Markdown("".join(buffer)))
            for chunk in chunks_iter:
                if not chunk:
                    continue
                buffer.append(chunk)
                live.update(Markdown("".join(buffer)))
            # If no chunks arrived, replace the frozen spinner with empty
            # content so it doesn't get stuck on screen.
            if not buffer:
                live.update(Text(""))
    finally:
        console.print()

    # Tiny dim footer so the user sees the stream actually finished and
    # gets a rough sense of cost — same idea as Claude CLI's per-turn
    # timing line. Skipped on empty streams so we don't print a footer
    # under nothing.
    if buffer:
        console.print(f"[dim]· {time.monotonic() - started:.1f}s[/dim]")

    return "".join(buffer)


__all__ = ["STREAM_LABEL_ANSWER", "STREAM_LABEL_ASSISTANT", "stream_to_console"]
