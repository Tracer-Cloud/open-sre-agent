"""REPL TTY plumbing: buffered print helpers and table factory.

Keeps cursor at column zero and normalises line endings under prompt_toolkit's
patch_stdout so Rich tables and JSON don't render as diagonal blocks.

Domain-specific table renderers live in :mod:`tables`.
"""

from __future__ import annotations

import io
import shutil
import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

_REPL_OUTPUT_PREPARED = ContextVar("_REPL_OUTPUT_PREPARED", default=False)


def _repl_output_already_prepared() -> bool:
    """Whether current call stack already prepared the TTY for Rich output."""
    return _REPL_OUTPUT_PREPARED.get()


def _console_print_prepared(console: Console, *objects: Any, **kwargs: Any) -> None:
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        console.print(*objects, **kwargs)
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)


def _repl_table_width(console: Console) -> int:
    """Best-effort terminal width for Rich tables after inline menu I/O."""
    term_cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    # Keep one safety column to avoid right-edge auto-wrap artifacts in some
    # terminals (first-char clipping / duplicate right border when a row lands
    # exactly on the terminal width).
    return max(40, min(console.width, term_cols) - 1)


def _prepare_tty_for_rich(console: Console) -> int:
    """Return the width Rich should render at.

    prepare_repl_output_line() (which writes \\r\\n) is intentionally NOT called
    here. Under patch_stdout(raw=True), that extra newline causes the bottom
    toolbar text to flush into the output stream before the table renders. Slash
    commands start after the user presses Enter, so the cursor is already on a
    fresh line; no extra line-feed is needed.
    """
    return _repl_table_width(console)


def _normalize_repl_line_endings(text: str) -> str:
    """Convert Rich output to ``\\r\\n`` so each line starts at column zero."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _write_repl_tty_buffered(
    *,
    width: int,
    leading_blank: bool,
    render_to_buffer: Callable[[Console], None],
) -> None:
    """Render Rich output to a buffer and write it in one TTY-safe stdout call."""
    buf = io.StringIO()
    buf_console = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        width=width,
    )
    render_to_buffer(buf_console)
    rendered = _normalize_repl_line_endings(buf.getvalue())
    if leading_blank:
        rendered = "\r\n" + rendered
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        sys.stdout.write(rendered)
        sys.stdout.flush()
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)


def print_repl_table(console: Console, table: Table, *, width: int | None = None) -> None:
    """Print a Rich table using REPL-safe TTY width.

    When the console writes to sys.stdout (the real REPL path), tables are
    rendered into a string buffer first and written in a single sys.stdout.write
    call with explicit \\r\\n line endings. This prevents the diagonal-render
    artifact that occurs under prompt_toolkit's patch_stdout: each table row is
    a separate Rich write, and if the terminal or proxy does not convert \\n to
    \\r\\n, every row starts where the previous one ended instead of column zero.

    When the console writes to a non-TTY stdout (piped output) or to a
    different file (e.g. a StringIO in tests), the normal console.print path
    is used — preserving the caller's color_system and avoiding ANSI pollution
    in piped output.
    """
    leading_blank = width is None
    width = width if width is not None else _prepare_tty_for_rich(console)
    if console.file is sys.stdout and sys.stdout.isatty():
        _write_repl_tty_buffered(
            width=width,
            leading_blank=leading_blank,
            render_to_buffer=lambda buf_console: buf_console.print(table),
        )
    else:
        if leading_blank:
            _console_print_prepared(console)
        _console_print_prepared(console, table, width=width)


def print_repl_json(console: Console, json_str: str) -> None:
    """Print JSON via Rich using REPL-safe \\r\\n line endings.

    Mirrors the buffered-write approach in :func:`print_repl_table` to prevent
    the diagonal-render artifact under prompt_toolkit's patch_stdout: bare
    ``\\n`` from Rich does not imply a carriage-return, so each JSON line would
    start at the column where the previous one ended.  Rendering to a buffer
    and normalising to ``\\r\\n`` ensures every line begins at column zero.
    The leading blank is included in the same write call to avoid a stale CPR
    sequence being left in stdin by a prompt_toolkit toolbar flush.
    """
    width = _prepare_tty_for_rich(console)
    if console.file is sys.stdout and sys.stdout.isatty():
        _write_repl_tty_buffered(
            width=width,
            leading_blank=True,
            render_to_buffer=lambda buf_console: buf_console.print_json(json_str),
        )
    else:
        token = _REPL_OUTPUT_PREPARED.set(True)
        try:
            console.print_json(json_str)
        finally:
            _REPL_OUTPUT_PREPARED.reset(token)


def repl_print(console: Console, *objects: Any, **kwargs: Any) -> None:
    """Print via Rich after resetting the TTY column (inline-menu safe)."""
    from app.cli.interactive_shell.ui.choice_menu import prepare_repl_output_line

    prepare_repl_output_line()
    _console_print_prepared(console, *objects, **kwargs)


def repl_table(**kwargs: Any) -> Table:
    """Minimal outer borders — closer to Claude Code than full ASCII grids."""
    opts: dict[str, Any] = {
        "box": box.MINIMAL_HEAVY_HEAD,
        "show_edge": False,
        "pad_edge": False,
        "title_justify": "left",
    }
    opts.update(kwargs)
    return Table(**opts)


__all__ = [
    "_repl_output_already_prepared",
    "_repl_table_width",
    "print_repl_json",
    "print_repl_table",
    "repl_print",
    "repl_table",
]
