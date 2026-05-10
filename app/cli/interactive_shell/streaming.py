"""Live token streaming for interactive-shell LLM responses.

Inside the persistent prompt_toolkit Application (#1679), the input box is
pinned at the bottom of the terminal via ``patch_stdout``. To keep the
input editable while a response streams (type-ahead), we cannot use
:class:`rich.live.Live` here — ``Live`` does cursor manipulation
(cursor-up + erase-line) for in-place redraw, which fights ``patch_stdout``
and blocks the input buffer from accepting keystrokes.

So this path streams chunks **silently**: chunks accumulate into a buffer
and the complete response is rendered as Markdown once the stream ends.
The streaming-progress indicator (``⟳ thinking… (Ns · ↓ Xk tokens)``) is
drawn by the persistent Application's status Window, which is updated by
``PersistentRepl._run_dispatch`` — orthogonal to this module.

Trade-off accepted: streaming loses on-the-fly Markdown formatting (no
live bold/headers/lists), but in exchange the input row stays pinned and
type-ahead works exactly as it does in Claude CLI's interactive surface.
The non-TTY path still renders the full Markdown at end — same as before.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from rich.console import Console
from rich.markdown import Markdown

from app.cli.interactive_shell.theme import BOLD_BRAND, DIM, MARKDOWN_THEME

# Approximate characters per token. Same heuristic as
# ``persistent_app.app._CHARS_PER_TOKEN`` — used here for the post-stream
# elapsed footer.
_CHARS_PER_TOKEN = 4

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


def _format_tokens(token_count: int) -> str:
    """Render the token count Claude-Code-style: ``42`` / ``1.2k`` / ``5.2k``."""
    if token_count >= 1000:
        return f"{token_count / 1000:.1f}k tokens"
    return f"{token_count} tokens"


def stream_to_console(
    console: Console,
    *,
    label: str,
    chunks: Iterator[str],
    suppress_if_starts_with: str | None = None,
) -> str:
    """Stream chunks to ``console`` and return the accumulated text.

    ``suppress_if_starts_with`` allows callers to skip live rendering when
    the first non-whitespace token indicates a machine-readable payload
    (e.g. JSON action plans). The return value still contains the full
    accumulated text in that case.
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

    def _next_chunk(it: Iterator[str]) -> str | None:
        try:
            return next(it)
        except StopIteration:
            return None

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

    console.print()
    console.print(f"[{BOLD_BRAND}]{label}:[/]")

    # Buffer chunks silently; the persistent Application's status Window
    # shows the streaming progress indicator. The ``finally`` ensures the
    # partial buffer renders even on exceptions so the caller can surface
    # an error label below it.
    buffer: list[str] = list(peeked)
    started = time.monotonic()
    # When ``console`` is the textual ``TextualConsole`` adapter, push
    # cumulative byte count into the ``StatusLine`` so the user sees the
    # ``thinking… (Ns · ↓ Xk tokens)`` indicator update live. ``getattr``
    # avoids importing the adapter here (would create an import cycle).
    # Throttled to ~10/s so the worker thread isn't bottlenecked queueing
    # ``call_from_thread`` events for every chunk on long streams. A
    # bare ``except`` makes failure here non-fatal — a flaky status
    # widget must never truncate the response buffer.
    progress_hook = getattr(console, "update_streaming_progress", None)
    total_bytes = sum(len(c) for c in peeked)
    last_progress_at = 0.0
    _PROGRESS_INTERVAL_S = 0.1

    def _maybe_update_progress(now: float, *, force: bool = False) -> float:
        nonlocal progress_hook
        if progress_hook is None:
            return last_progress_at
        if not force and now - last_progress_at < _PROGRESS_INTERVAL_S:
            return last_progress_at
        try:
            progress_hook(total_bytes)
        except Exception:
            progress_hook = None
        return now

    def _is_cancelled() -> bool:
        # ``getattr`` keeps this layer decoupled from the loop's
        # ``_StreamingConsole`` — non-interactive callers (the test
        # harness, the non-TTY path above) never expose the attribute
        # so this stays False for them.
        return bool(getattr(console, "cancel_requested", False))

    if peeked:
        last_progress_at = _maybe_update_progress(time.monotonic(), force=True)
    try:
        while True:
            if _is_cancelled():
                break
            chunk = _next_chunk(chunks_iter)
            if chunk is None:
                break
            if not chunk:
                continue
            buffer.append(chunk)
            total_bytes += len(chunk)
            last_progress_at = _maybe_update_progress(time.monotonic())
    finally:
        elapsed = time.monotonic() - started
        if buffer:
            with console.use_theme(MARKDOWN_THEME):
                console.print(Markdown("".join(buffer), code_theme="ansi_dark"))
            tokens = _format_tokens(sum(len(c) for c in buffer) // _CHARS_PER_TOKEN)
            console.print(f"[{DIM}]· {elapsed:.1f}s · ↓ {tokens}[/]")
        console.print()

    return "".join(buffer)


__all__ = ["STREAM_LABEL_ANSWER", "STREAM_LABEL_ASSISTANT", "stream_to_console"]
