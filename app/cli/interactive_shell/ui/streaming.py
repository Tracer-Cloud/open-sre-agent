"""Live token streaming for interactive-shell LLM responses.

The interactive REPL pins the input box at the bottom of the terminal
via ``patch_stdout``. To keep the input editable while a response
streams (type-ahead) we can't use :class:`rich.live.Live` — ``Live``
does cursor manipulation (cursor-up + erase-line) for in-place redraw,
which fights ``patch_stdout`` and blocks the input buffer from
accepting keystrokes.

Instead this path streams **paragraph-by-paragraph**: chunks accumulate
in ``para_buffer`` and a complete paragraph (text up to the next
``\\n\\n`` outside an open code-fence) renders as ``rich.Markdown`` the
moment its boundary is seen. The trailing partial paragraph is
force-flushed at end-of-stream. Code blocks are kept whole — we never
split on ``\\n\\n`` while a triple-backtick fence is unclosed.

Streaming progress and cancellation are surfaced through optional
attributes on the ``console``: ``update_streaming_progress(bytes)`` is
called per chunk (throttled to ~10/s) so the bottom-toolbar token
counter updates live, and ``cancel_requested`` is polled between chunks
so an Esc press in the prompt cancels promptly. The ``getattr``
indirection keeps this module decoupled from the loop's
``_StreamingConsole`` shim.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

from rich.console import Console
from rich.markdown import Markdown

from app.cli.interactive_shell.ui.theme import BOLD_BRAND, DIM, MARKDOWN_THEME

# Approximate characters per token. Single source of truth for the
# streaming layer and ``loop._SpinnerState`` (which imports this so the
# live spinner and the post-stream footer can't drift apart).
_CHARS_PER_TOKEN = 4

# Throttle for the optional ``update_streaming_progress`` hook on the
# console — caps cross-thread queueing on long bursts of chunks. Same
# value (and intent) as ``loop._PROMPT_REFRESH_INTERVAL_S``.
_PROGRESS_INTERVAL_S = 0.1

# Markdown rendering constants — extracted so streaming.py and any
# external caller (e.g. agent_actions.py for the planned-actions
# bullet header) stay in lock-step.
_PARAGRAPH_BREAK = "\n\n"
_CODE_FENCE = "```"
# Match a triple-backtick only when it opens a line. An inline mention
# inside flowing text (e.g. "The ``` marker opens a code block") would
# otherwise flip the odd/even fence count below and stall paragraph
# rendering until end-of-stream. CommonMark's fence syntax requires
# the fence to be at line start anyway, so this is a tighter and
# more accurate check than a naive substring count.
_CODE_FENCE_LINE_RE = re.compile(rf"^{re.escape(_CODE_FENCE)}", re.MULTILINE)
_MARKDOWN_CODE_THEME = "ansi_dark"

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


def render_response_header(console: Console, label: str) -> None:
    """Print the ``●`` bullet row marker that opens every assistant
    response (Claude Code-style row layout). Shared with
    ``agent_actions.execute_cli_actions`` so the planned-actions path
    and the streaming response path use the exact same prefix.
    """
    console.print(f"[{BOLD_BRAND}]●[/] [{DIM}]{label}[/]")


def format_token_count_short(token_count: int) -> str:
    """Format a token count as a short string — ``42`` / ``1.2k`` / ``5.2k``.

    Shared with :class:`app.cli.interactive_shell.loop._SpinnerState` so
    the streaming footer (``· 9.5s · ↓ 1.2k tokens``) and the live
    spinner (``⠋ thinking… (5s · ↓ 1.2k tokens)``) format identically.
    """
    if token_count >= 1000:
        return f"{token_count / 1000:.1f}k"
    return str(token_count)


def _format_tokens(token_count: int) -> str:
    return f"{format_token_count_short(token_count)} tokens"


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
            render_response_header(console, label)
            with console.use_theme(MARKDOWN_THEME):
                console.print(Markdown(text, code_theme=_MARKDOWN_CODE_THEME))
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
    render_response_header(console, label)

    # Paragraph-level streaming: chunks accumulate in ``para_buffer``
    # until a paragraph boundary (``\n\n`` outside a code block) closes
    # the paragraph, at which point we render that paragraph as
    # Markdown via ``console.print(Markdown(...))``. Visible "streaming"
    # is per-paragraph rather than per-chunk — a true live re-render
    # would need cursor manipulation that fights ``patch_stdout``. The
    # spinner (``⠋ thinking… (Ns · ↓ X tokens)``) ticks during long
    # paragraphs to confirm chunks are still arriving, and code blocks
    # are kept whole (we never split on ``\n\n`` while a fence is open).
    buffer: list[str] = list(peeked)
    para_buffer: list[str] = list(peeked)
    started = time.monotonic()
    progress_hook = getattr(console, "update_streaming_progress", None)
    total_bytes = sum(len(c) for c in peeked)
    last_progress_at = 0.0

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

    def _render_paragraph(text: str) -> None:
        if not text.strip():
            return
        with console.use_theme(MARKDOWN_THEME):
            console.print(Markdown(text.rstrip(), code_theme=_MARKDOWN_CODE_THEME))

    def _flush_paragraphs(*, force: bool = False) -> None:
        """Emit any complete paragraphs from ``para_buffer``.

        Splits on ``\\n\\n`` (``_PARAGRAPH_BREAK``) but only when an
        even number of triple-backtick fences (``_CODE_FENCE``) are
        present in the proposed prefix — that's enough to keep code
        blocks whole without tracking fence type. A ``\\n\\n`` falling
        inside an open fence is skipped so we keep scanning forward;
        otherwise a code block with embedded blank lines would defer
        every later paragraph to ``force=True`` at EOS. ``force``
        flushes any remaining buffer at end-of-stream.
        """
        nonlocal para_buffer
        break_len = len(_PARAGRAPH_BREAK)
        while True:
            text = "".join(para_buffer)
            search_from = 0
            rendered = False
            while True:
                idx = text.find(_PARAGRAPH_BREAK, search_from)
                if idx < 0:
                    break
                paragraph = text[: idx + break_len]
                # Odd line-start fence count means a fence is still
                # open; the boundary is inside it, so skip and keep
                # scanning for the next ``\n\n`` that lands outside
                # any fence. Only line-start fences count (per
                # CommonMark), so an inline mention like
                # ``Use ``` to open a block`` doesn't trip this check.
                if len(_CODE_FENCE_LINE_RE.findall(paragraph)) % 2 == 1:
                    search_from = idx + break_len
                    continue
                _render_paragraph(paragraph)
                tail = text[idx + break_len :]
                para_buffer = [tail] if tail else []
                rendered = True
                break
            if not rendered:
                break
        if force:
            tail = "".join(para_buffer)
            if tail.strip():
                _render_paragraph(tail)
            para_buffer = []

    def _maybe_flush_after_append(chunk: str, prev_chunk: str | None) -> None:
        """Cheap fast-path before the O(buffer) join inside ``_flush_paragraphs``.

        A paragraph boundary requires ``\\n\\n``. The second ``\\n``
        must be in the *current* chunk (any earlier chunks were already
        flushed or had no boundary). Skip the full flush when ``\\n``
        is absent here AND the chunk-to-chunk seam can't form a
        boundary either. Without this guard, a long single-paragraph
        response (e.g. 4k chunks, no blank-line separators) becomes
        O(n²) because every chunk triggers a full
        ``"".join(para_buffer)``.

        ``prev_chunk`` is the chunk immediately before this one — the
        caller threads it explicitly so we don't reach into
        ``para_buffer[-2]`` and read like magic indexing.
        """
        if not chunk:
            return
        if _PARAGRAPH_BREAK in chunk:
            _flush_paragraphs()
            return
        # Cross-chunk boundary: previous chunk ended with ``\n`` and
        # this chunk's leading ``\n`` completes the ``\n\n``.
        newline = _PARAGRAPH_BREAK[0]
        if (
            prev_chunk is not None
            and newline in chunk
            and prev_chunk.endswith(newline)
            and chunk.startswith(newline)
        ):
            _flush_paragraphs()

    if peeked:
        last_progress_at = _maybe_update_progress(time.monotonic(), force=True)
        _flush_paragraphs()

    # Track the chunk immediately preceding the current one so the
    # cross-chunk seam check can detect ``\n\n`` straddling the
    # boundary without reaching into ``para_buffer`` by index.
    prev_chunk: str | None = peeked[-1] if peeked else None
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
            para_buffer.append(chunk)
            total_bytes += len(chunk)
            last_progress_at = _maybe_update_progress(time.monotonic())
            _maybe_flush_after_append(chunk, prev_chunk)
            prev_chunk = chunk
    finally:
        # Render whatever's left in the paragraph buffer so the user
        # sees the full response even if it didn't end on ``\n\n``.
        _flush_paragraphs(force=True)
        elapsed = time.monotonic() - started
        if buffer:
            tokens = _format_tokens(total_bytes // _CHARS_PER_TOKEN)
            console.print(f"[{DIM}]· {elapsed:.1f}s · ↓ {tokens}[/]")
        console.print()

    return "".join(buffer)


__all__ = [
    "STREAM_LABEL_ANSWER",
    "STREAM_LABEL_ASSISTANT",
    "format_token_count_short",
    "render_response_header",
    "stream_to_console",
]
