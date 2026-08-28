"""Interactive choice helpers for TTY-first REPL flows.

Inline menus render in the terminal scrollback (below the submitted command),
not as a separate prompt-toolkit full-screen dialog — important when the REPL
already runs under asyncio.

Each menu erases itself on exit (selection or Esc) so nested menus never
pile up — only the result output and the next level appear on screen.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Literal

from rich.console import Console
from rich.markup import escape

import infrastructure.terminal.theme as ui_theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.key_reader import read_key_unix, read_key_windows

_HINT = "↑↓ navigate    Enter select    Esc cancel"
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
    console.rule(characters="─", style=str(ui_theme.DIM))
    console.print()


# ── raw key reader ───────────────────────────────────────────────────────────


def _read_action() -> MenuAction:
    """Map a raw keypress to a menu action.

    Delegates terminal I/O to :mod:`key_reader` and applies
    choice_menu-specific overrides: Tab → ``"down"``,
    right-arrow → ``"enter"``, left-arrow → ``"ignore"``.
    """
    key = read_key_windows() if os.name == "nt" else read_key_unix()
    if key == "tab":
        return "down"
    if key == "right":
        return "enter"
    if key == "left":
        return "ignore"
    return key  # type: ignore[return-value]


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


def _sanitize_menu(
    title: str,
    crumb: str,
    labels: list[str],
) -> tuple[str, str, list[str]]:
    """Strip model-supplied controls before raw ANSI write or row counting."""
    return (
        strip_terminal_controls(title),
        strip_terminal_controls(crumb),
        [strip_terminal_controls(label) for label in labels],
    )


def _menu_height(crumb: str, labels: list[str]) -> int:
    # leading, title, [crumb], rule, blank, choices, blank, hint
    return _MENU_LEADING_LINES + 1 + (1 if crumb else 0) + 1 + 1 + len(labels) + 1 + 1


def write_menu_line(text: str = "") -> None:
    """Write one inline-menu line at column zero even while the terminal is in raw mode."""
    if text:
        sys.stdout.write(f"\r{text}{_TERMINAL_NEWLINE}")
        return
    sys.stdout.write(_TERMINAL_NEWLINE)


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


def leave_inline_menu() -> None:
    """Restore cooked stdin and start the next Rich line at column zero.

    Call after every inline menu exits (select or Esc). Without this, padded
    menu rows leave the cursor mid-line and later prints (cancelled notice,
    ``/exit`` resume hint) stagger diagonally.
    """
    from surfaces.shared.terminal.components.key_reader import restore_stdin_terminal

    restore_stdin_terminal()
    prepare_repl_output_line()


def erase_menu_lines(height: int) -> None:
    """Erase a previously-rendered inline menu block."""
    _erase_menu_block(height)


def _clear_prompt_toolkit_paint() -> None:
    """Drop any live prompt-toolkit frame so option menus own the screen alone."""
    from contextlib import suppress

    try:
        from prompt_toolkit.application.current import get_app_or_none
    except ImportError:
        return
    app = get_app_or_none()
    if app is None:
        return
    renderer = getattr(app, "renderer", None)
    if renderer is not None:
        # Erase only the app's reserved rows so the transcript stays and the
        # menu draws inline (Droid-style); a full clear reads as a new window.
        with suppress(Exception):
            renderer.erase()
    if getattr(app, "is_running", False):
        with suppress(Exception):
            app.invalidate()


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
    title, crumb, labels = _sanitize_menu(title, crumb, labels)
    if erase_lines:
        _erase_menu_block(erase_lines)
    for _ in range(_MENU_LEADING_LINES):
        write_menu_line()
    # title
    write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{title}{ui_theme.ANSI_RESET}")
    # breadcrumb path
    if crumb:
        write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{crumb}{ui_theme.ANSI_RESET}")
    # separator below header
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{_rule(w)}{ui_theme.ANSI_RESET}")
    write_menu_line()
    # choices
    for i, label in enumerate(labels):
        here = i == index
        numbered = f"{i + 1}. {label}"
        sym = "❯" if here else " "
        padded = _pad(sym, numbered, w)
        if here:
            write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{padded}{ui_theme.ANSI_RESET}")
        else:
            write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{padded}{ui_theme.ANSI_RESET}")
    write_menu_line()
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{_HINT}{ui_theme.ANSI_RESET}")
    out.flush()


def _erase_menu(crumb: str, labels: list[str]) -> None:
    """Move cursor up to the start of this menu block and wipe it."""
    _, crumb, labels = _sanitize_menu("", crumb, labels)
    height = _menu_height(crumb, labels)
    _erase_menu_block(height)
    sys.stdout.flush()


# ── picker loop ──────────────────────────────────────────────────────────────


def _pick(
    *,
    title: str,
    crumb: str,
    labels: list[str],
    initial_index: int = 0,
    custom_label: str | None = None,
) -> int | str | None:
    """Draw an inline menu; return index, custom typed string, or None on Esc.

    When ``custom_label`` matches the focused row, printable keys type on that
    row in place (Droid-style) and Enter returns the typed string.
    """
    from surfaces.shared.terminal.components.key_reader import read_menu_or_char

    if not labels:
        return None
    title, crumb, labels = _sanitize_menu(title, crumb, labels)
    idx = initial_index % len(labels)
    height = _menu_height(crumb, labels)
    draft = ""
    first = True
    while True:
        on_custom = custom_label is not None and labels[idx] == custom_label
        display = list(labels)
        if on_custom:
            display[idx] = f"{draft}█"
        _draw_menu(
            title=title,
            crumb=crumb,
            labels=display,
            index=idx,
            erase_lines=0 if first else height,
        )
        first = False
        height = _menu_height(crumb, display)
        action = (
            read_menu_or_char(allow_chars=True) if on_custom else _read_action()
        )
        if on_custom and action == "backspace":
            draft = draft[:-1]
            continue
        if on_custom and len(action) == 1 and action.isprintable() and action not in "\t\n\r":
            draft += action
            continue
        if action == "enter":
            if on_custom:
                text = draft.strip()
                if not text:
                    continue
                _erase_menu(crumb, display)
                leave_inline_menu()
                return text
            _erase_menu(crumb, labels)
            leave_inline_menu()
            return idx
        if action in ("cancel", "eof"):
            _erase_menu(crumb, display if on_custom else labels)
            leave_inline_menu()
            return None
        if action == "ignore":
            continue
        if action == "up":
            idx = (idx - 1) % len(labels)
        elif action == "down":
            idx = (idx + 1) % len(labels)


def repl_choose_one(
    *,
    title: str,
    choices: list[tuple[str, str]],
    breadcrumb: str = "",
    initial_value: str | None = None,
    custom_label: str | None = None,
) -> str | None:
    """Show an inline erasing arrow-key menu; return selected value or None on Esc.

    ``breadcrumb`` is a slash-separated path shown dimly below the title, e.g.
    ``/model › set``.  Only call when :func:`repl_tty_interactive` is True.

    When ``custom_label`` is set and that row is focused, the user types on that
    row in place (same option array) instead of opening a separate prompt.
    """
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

    if not choices or not repl_tty_interactive():
        return None
    _clear_prompt_toolkit_paint()
    drain_stale_cpr_bytes()
    crumb = breadcrumb
    labels = [label for _value, label in choices]
    initial_index = 0
    if initial_value is not None:
        for index, (value, _label) in enumerate(choices):
            if value == initial_value:
                initial_index = index
                break
    picked = _pick(
        title=title,
        crumb=crumb,
        labels=labels,
        initial_index=initial_index,
        custom_label=custom_label,
    )
    if picked is None:
        return None
    if isinstance(picked, str):
        return picked
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
    title = escape(strip_terminal_controls(title))
    console.print(f"[{ui_theme.SECONDARY}]{title}[/]")
    for index, choice in enumerate(choices, start=1):
        safe = escape(strip_terminal_controls(choice))
        console.print(f"[{ui_theme.SECONDARY}]  {index}. {safe}[/]")


__all__ = [
    "CRUMB_SEP",
    "erase_menu_lines",
    "leave_inline_menu",
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
