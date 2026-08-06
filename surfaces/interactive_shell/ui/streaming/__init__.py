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
indirection keeps this module decoupled from the ``StreamingConsole`` adapter.

Gather answers may set ``defer_want_me_to_closer`` so dual/drifted Want-me-to
menus are not painted until the harness flushes the canonical closer.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass

from rich.console import Console
from rich.markdown import Markdown

import platform.terminal.theme as ui_theme
from core.agent_harness.prompts.rules import normalize_three_tier_spacing
from core.agent_harness.session.want_me_to import WANT_ME_TO_MARKER, closer_tail_from
from surfaces.interactive_shell.ui.components.token_format import (
    _CHARS_PER_TOKEN,
    format_token_count_short,
)
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole

# Throttle for the optional ``update_streaming_progress`` hook on the
# console — caps cross-thread queueing on long bursts of chunks. Same
# value (and intent) as ``runtime.core.state.PROMPT_REFRESH_INTERVAL_S``.
_PROGRESS_INTERVAL_SECONDS = 0.1

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
# Rich inserts leading vertical space when these block types start a standalone
# Markdown render. Other blocks do not, so paragraph-by-paragraph streaming must
# add the consumed ``\n\n`` before rendering them.
_SELF_SPACING_BLOCK_TOKEN_TYPES = frozenset(
    {
        "blockquote_open",
        "bullet_list_open",
        "ordered_list_open",
        "table_open",
    }
)

# Rich Markdown treats ``__init__.py`` as bold emphasis around ``init``, which
# strips the underscores and restyles that span. Escape dunder filenames so
# path-heavy reports (architecture audits, etc.) keep uniform body color.
_DUNDER_FILENAME_RE = re.compile(r"__([A-Za-z0-9_]+)__(?=\.py\b)")


def _escape_markdown_dunder_filenames(text: str) -> str:
    """Neutralize ``__name__.py`` so Markdown does not parse it as strong emphasis."""
    return _DUNDER_FILENAME_RE.sub(r"\_\_\1\_\_", text)


STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"


def _build_markdown_block(text: str) -> Markdown:
    """Build a Markdown renderable with the shared escaping and code theme."""
    spaced = normalize_three_tier_spacing(text)
    return Markdown(
        _escape_markdown_dunder_filenames(spaced.rstrip()),
        code_theme=ui_theme.MARKDOWN_CODE_THEME,
    )


def render_markdown_block(console: Console, text: str) -> None:
    """Render one complete Markdown block using the shared markdown theme.

    The single rendering path for model prose that arrives whole (not
    chunk-streamed) — e.g. the action agent's intermediate phase headers —
    so every markdown surface shares one escaping/theme policy.
    """
    if not text.strip():
        return
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(_build_markdown_block(text))


def render_response_header(console: Console, label: str) -> None:
    """Print the ``●`` bullet row marker that opens every assistant
    response (Claude Code-style row layout). Shared with
    ``action_turn.run_action_tool_turn`` so the planned-actions path
    and the streaming response path use the exact same prefix.
    """
    console.print(f"[{ui_theme.BOLD_BRAND}]●[/] [{ui_theme.DIM}]{label}[/]")


def _format_tokens(token_count: int) -> str:
    return f"{format_token_count_short(token_count)} tokens"


def _paragraph_has_want_me_to(text: str) -> bool:
    return WANT_ME_TO_MARKER in text.lower()


@dataclass(frozen=True)
class StreamPaintResult:
    """Accumulated stream text plus whether the Want-me-to closer was held back."""

    text: str
    deferred_closer: bool = False
    footer_elapsed_s: float | None = None
    footer_total_bytes: int | None = None


def stream_to_console(
    console: Console,
    *,
    label: str,
    chunks: Iterator[str],
    suppress_if_starts_with: str | None = None,
    defer_want_me_to_closer: bool = False,
) -> str:
    """Stream chunks to ``console`` and return the accumulated text.

    ``suppress_if_starts_with`` allows callers to skip live rendering when
    the first non-whitespace token indicates a machine-readable payload
    (e.g. machine-readable payloads). The return value still contains the full
    accumulated text in that case.

    When ``defer_want_me_to_closer`` is true, paragraphs containing a Want-me-to
    closer are not painted (and the timing footer is held) so the caller can
    flush a normalized closer via :func:`finish_deferred_closer`.
    """
    return stream_to_console_state(
        console,
        label=label,
        chunks=chunks,
        suppress_if_starts_with=suppress_if_starts_with,
        defer_want_me_to_closer=defer_want_me_to_closer,
    ).text


def stream_to_console_state(
    console: Console,
    *,
    label: str,
    chunks: Iterator[str],
    suppress_if_starts_with: str | None = None,
    defer_want_me_to_closer: bool = False,
) -> StreamPaintResult:
    """Like :func:`stream_to_console` but returns paint/defer metadata."""
    if not console.is_terminal:
        text = "".join(chunks)
        if suppress_if_starts_with is not None and text.lstrip().startswith(
            suppress_if_starts_with
        ):
            return StreamPaintResult(text=text)
        if not text:
            return StreamPaintResult(text=text)
        if defer_want_me_to_closer and _paragraph_has_want_me_to(text):
            # Non-TTY: hold the whole paint when a closer is present so the
            # gather path can print the canonical rewrite once.
            return StreamPaintResult(text=text, deferred_closer=True)
        console.print()
        render_response_header(console, label)
        render_markdown_block(console, text)
        console.print()
        return StreamPaintResult(text=text)

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
                return StreamPaintResult(text="".join(peeked) + "".join(drained))
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
    rendered_paragraphs = 0
    deferred_closer = False

    def _maybe_update_progress(now: float, *, force: bool = False) -> float:
        nonlocal progress_hook
        if progress_hook is None:
            return last_progress_at
        if not force and now - last_progress_at < _PROGRESS_INTERVAL_SECONDS:
            return last_progress_at
        try:
            progress_hook(total_bytes)
        except Exception:
            progress_hook = None
        return now

    def _is_cancelled() -> bool:
        # ``getattr`` keeps this layer decoupled from the loop's
        # ``StreamingConsole`` — non-interactive callers (the test
        # harness, the non-TTY path above) never expose the attribute
        # so this stays False for them.
        return bool(getattr(console, "cancel_requested", False))

    def _paint_paragraph_body(text: str) -> None:
        nonlocal rendered_paragraphs
        if not text.strip():
            return
        markdown = _build_markdown_block(text)
        starts_with_self_spacing_block = bool(
            markdown.parsed and markdown.parsed[0].type in _SELF_SPACING_BLOCK_TOKEN_TYPES
        )
        if rendered_paragraphs and not starts_with_self_spacing_block:
            # ``_flush_paragraphs`` consumes the source ``\n\n`` boundary.
            # Restore it explicitly unless Rich adds equivalent leading space
            # for the next standalone block. This matters most after lists,
            # whose renderer adds no trailing blank line.
            console.print()
        with console.use_theme(ui_theme.MARKDOWN_THEME):
            console.print(markdown)
        rendered_paragraphs += 1

    def _render_paragraph(text: str) -> None:
        nonlocal deferred_closer
        if not text.strip():
            return
        if defer_want_me_to_closer and _paragraph_has_want_me_to(text):
            deferred_closer = True
            # Keep evidence / questions visible; hold only from Want-me-to onward.
            lowered = text.lower()
            pos = lowered.find(WANT_ME_TO_MARKER)
            start = pos
            if start >= 2 and text[start - 2 : start] == "**":
                start -= 2
            head = text[:start].rstrip() if start > 0 else ""
            if head.strip():
                _paint_paragraph_body(head)
            return
        _paint_paragraph_body(text)

    def _flush_paragraphs(*, force: bool = False) -> None:
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
    text = "".join(buffer)
    if deferred_closer:
        # Footer after the canonical closer is flushed by the caller.
        return StreamPaintResult(
            text=text,
            deferred_closer=True,
            footer_elapsed_s=elapsed,
            footer_total_bytes=total_bytes,
        )
    if buffer:
        tokens = _format_tokens(total_bytes // _CHARS_PER_TOKEN)
        console.print(f"[{ui_theme.DIM}]· {elapsed:.1f}s · ↓ {tokens}[/]")
    console.print()
    return StreamPaintResult(text=text, deferred_closer=False)


def publish_full_response(console: Console, text: str, *, label: str = "assistant") -> None:
    """Paint a complete assistant answer (non-TTY deferred gather path)."""
    body = (text or "").strip()
    if not body:
        return
    console.print()
    render_response_header(console, label)
    render_markdown_block(console, body)
    console.print()


def finish_deferred_closer(
    console: Console,
    final_text: str,
    *,
    footer_elapsed_s: float | None = None,
    footer_total_bytes: int | None = None,
) -> None:
    """Paint the (possibly rewritten) Want-me-to closer + held stream footer."""
    closer = closer_tail_from(final_text)
    if closer:
        render_markdown_block(console, closer)
    if footer_elapsed_s is not None and footer_total_bytes is not None:
        tokens = _format_tokens(footer_total_bytes // _CHARS_PER_TOKEN)
        console.print(f"[{ui_theme.DIM}]· {footer_elapsed_s:.1f}s · ↓ {tokens}[/]")
    console.print()


__all__ = [
    "StreamingConsole",
    "STREAM_LABEL_ANSWER",
    "STREAM_LABEL_ASSISTANT",
    "StreamPaintResult",
    "finish_deferred_closer",
    "format_token_count_short",
    "publish_full_response",
    "render_markdown_block",
    "render_response_header",
    "stream_to_console",
    "stream_to_console_state",
]
