"""Interactive choice helpers for TTY-first REPL flows.

Inline menus render in the terminal scrollback (below the submitted command),
not as a separate prompt-toolkit full-screen dialog — important when the REPL
already runs under asyncio.

Each menu erases itself on exit (selection or Esc) so nested menus never
pile up — only the result output and the next level appear on screen.
"""

from __future__ import annotations

import os
import select
import shutil
import sys
from typing import Any, Literal, cast

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.ui.theme import (
    ANSI_RESET,
    DIM,
    DIM_COUNTER_ANSI,
    MENU_SELECTION_ROW_ANSI,
    PROMPT_ACCENT_ANSI,
    SECONDARY,
)

_HINT = "↑↓/j/k  Enter/Space  Esc/q"
CRUMB_SEP = "  ›  "
# Blank line after the submitted slash line before the menu header (all pickers).
_MENU_LEADING_LINES = 1
_TERMINAL_NEWLINE = "\r\n"
MenuAction = Literal["up", "down", "enter", "cancel", "eof", "ignore"]


def repl_tty_interactive() -> bool:
    """Return True when stdin/stdout support an interactive picker UI."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def ensure_tty_column_zero() -> None:
    """Reset the cursor column before Rich output when a TTY is active."""
    if repl_tty_interactive():
        reset_tty_column()


def prepare_repl_output_line() -> None:
    """Begin Rich output on a new line after inline menu I/O."""
    if repl_tty_interactive():
        sys.stdout.write(_TERMINAL_NEWLINE)
        reset_tty_column()


def repl_section_break(console: Console) -> None:
    """Blank line + dim rule between an inline menu step and Rich output."""
    prepare_repl_output_line()
    console.print()
    console.rule(characters="─", style=DIM)
    console.print()


# ── raw key reader ───────────────────────────────────────────────────────────


def _read_action() -> MenuAction:
    """Return up | down | enter | cancel | eof."""
    if os.name == "nt":
        import msvcrt

        c = msvcrt.getch()  # type: ignore[attr-defined]
        if c in (b"\x03",):
            return "cancel"
        if c in (b"\r", b"\n", b" "):
            return "enter"
        if c in (b"j", b"J"):
            return "down"
        if c in (b"k", b"K"):
            return "up"
        if c in (b"q", b"Q"):
            return "cancel"
        if c in (b"\xe0", b"\x00"):
            c2 = msvcrt.getch()  # type: ignore[attr-defined]
            if c2 == b"H":
                return "up"
            if c2 == b"P":
                return "down"
            if c2 == b"M":
                return "enter"
            if c2 == b"K":
                return "ignore"
            return "ignore"
        if c == b"\x1b":
            return "cancel"
        return "ignore"

    import termios
    import tty

    fd = sys.stdin.fileno()
    # ``termios`` / ``tty`` are POSIX-only; stubs expose no attributes when
    # typechecking with ``mypy --platform win32``.
    old_attrs: Any = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]
        data = os.read(fd, 1)
        if not data:
            return "eof"
        key_code = cast(int, data[0])
        if key_code in (3, 4):
            return "cancel"
        if key_code in (10, 13, 32):
            return "enter"
        if data in (b"j", b"J"):
            return "down"
        if data in (b"k", b"K"):
            return "up"
        if data in (b"q", b"Q"):
            return "cancel"
        if key_code == 27:
            if select.select([fd], [], [], 0.05)[0]:
                seq = os.read(fd, 1)
                if seq == b"[":
                    arrow = os.read(fd, 1)
                    if arrow == b"A":
                        return "up"
                    if arrow == b"B":
                        return "down"
                    if arrow == b"C":
                        return "enter"
                    if arrow == b"D":
                        return "ignore"
            return "cancel"
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)  # type: ignore[attr-defined]


def read_menu_action() -> MenuAction:
    """Read one normalized inline-menu action from stdin."""
    return _read_action()


# ── rendering helpers ────────────────────────────────────────────────────────


def _cols() -> int:
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def menu_columns() -> int:
    """Return the current terminal width floor used by inline menus."""
    return _cols()


def _rule(width: int) -> str:
    return "─" * width


def _pad(sym: str, label: str, width: int) -> str:
    content = f" {sym} {label}"
    pad = width - len(content)
    return content + (" " * pad if pad > 0 else "")


def _menu_height(crumb: str, labels: list[str]) -> int:
    # leading, title, [crumb], rule, blank, choices, blank, hint
    return _MENU_LEADING_LINES + 1 + (1 if crumb else 0) + 1 + 1 + len(labels) + 1 + 1


def _write_menu_line(text: str = "") -> None:
    """Write a menu line at column zero even while the terminal is in raw mode."""
    if text:
        sys.stdout.write(f"\r{text}{_TERMINAL_NEWLINE}")
        return
    sys.stdout.write(_TERMINAL_NEWLINE)


def write_menu_line(text: str = "") -> None:
    """Write one inline-menu line at column zero."""
    _write_menu_line(text)


def _erase_menu_block(height: int) -> None:
    if height:
        sys.stdout.write(f"\r\x1b[{height}A\r\x1b[J")
    reset_tty_column()


def reset_tty_column() -> None:
    """Return the cursor to column zero after inline menu I/O.

    Menu rows are padded to the terminal width, so the cursor often ends on a
    high column. Rich output that follows must start at column zero or tables
    render as a diagonal block of leading whitespace.
    """
    sys.stdout.write("\r")
    sys.stdout.flush()


def erase_menu_lines(height: int) -> None:
    """Erase a previously-rendered inline menu block."""
    _erase_menu_block(height)


def _draw_menu(
    *,
    title: str,
    crumb: str,
    labels: list[str],
    index: int,
    erase_lines: int,
) -> None:
    out = sys.stdout
    w = _cols()
    if erase_lines:
        _erase_menu_block(erase_lines)
    for _ in range(_MENU_LEADING_LINES):
        _write_menu_line()
    # title
    _write_menu_line(f"{PROMPT_ACCENT_ANSI}{title}{ANSI_RESET}")
    # breadcrumb path
    if crumb:
        _write_menu_line(f"{DIM_COUNTER_ANSI}{crumb}{ANSI_RESET}")
    # separator below header
    _write_menu_line(f"{DIM_COUNTER_ANSI}{_rule(w)}{ANSI_RESET}")
    _write_menu_line()
    # choices
    for i, label in enumerate(labels):
        here = i == index
        sym = ">" if here else " "
        padded = _pad(sym, label, w)
        if here:
            _write_menu_line(f"{MENU_SELECTION_ROW_ANSI}{padded}{ANSI_RESET}")
        else:
            _write_menu_line(f"{DIM_COUNTER_ANSI}{padded}{ANSI_RESET}")
    _write_menu_line()
    _write_menu_line(f"{DIM_COUNTER_ANSI}{_HINT}{ANSI_RESET}")
    out.flush()


def _erase_menu(crumb: str, labels: list[str]) -> None:
    """Move cursor up to the start of this menu block and wipe it."""
    height = _menu_height(crumb, labels)
    _erase_menu_block(height)
    sys.stdout.flush()


# ── picker loop ──────────────────────────────────────────────────────────────


def _pick(*, title: str, crumb: str, labels: list[str]) -> int | None:
    """Draw an inline menu, let user navigate, erase on exit. Returns index or None."""
    if not labels:
        return None
    idx = 0
    height = _menu_height(crumb, labels)
    first = True
    while True:
        _draw_menu(
            title=title,
            crumb=crumb,
            labels=labels,
            index=idx,
            erase_lines=0 if first else height,
        )
        first = False
        action = _read_action()
        if action == "enter":
            _erase_menu(crumb, labels)
            return idx
        if action in ("cancel", "eof"):
            _erase_menu(crumb, labels)
            return None
        if action == "ignore":
            continue
        if action == "up":
            idx = (idx - 1) % len(labels)
        elif action == "down":
            idx = (idx + 1) % len(labels)


# ── public API ───────────────────────────────────────────────────────────────


def repl_choose_one(
    *,
    title: str,
    choices: list[tuple[str, str]],
    breadcrumb: str = "",
) -> str | None:
    """Show an inline erasing arrow-key menu; return selected value or None on Esc.

    ``breadcrumb`` is a slash-separated path shown dimly below the title, e.g.
    ``/model › set``.  Only call when :func:`repl_tty_interactive` is True.
    """
    if not choices or not repl_tty_interactive():
        return None
    crumb = breadcrumb
    labels = [label for _value, label in choices]
    picked = _pick(title=title, crumb=crumb, labels=labels)
    if picked is None:
        return None
    value = choices[picked][0]
    return value if isinstance(value, str) else None


def print_valid_choice_list(
    console: Console,
    *,
    title: str,
    choices: list[str],
) -> None:
    """Print one choice per line for scan-friendly fallback/error messaging."""
    if not choices:
        return
    console.print(f"[{SECONDARY}]{title}[/]")
    for choice in choices:
        console.print(f"[{SECONDARY}]  - {escape(choice)}[/]")


__all__ = [
    "CRUMB_SEP",
    "erase_menu_lines",
    "menu_columns",
    "print_valid_choice_list",
    "read_menu_action",
    "repl_choose_one",
    "ensure_tty_column_zero",
    "prepare_repl_output_line",
    "repl_section_break",
    "repl_tty_interactive",
    "reset_tty_column",
    "write_menu_line",
]
