"""The raw-terminal select widget and prompts used on the CLI (console-less) path.

Why a custom select menu instead of repl_choose_one() on the CLI path:
  Rich's Live renderer leaves the cursor at an indeterminate row.
  choice_menu._erase_menu_block() assumes a fixed cursor position and can
  redraw in the wrong place after streaming output ends.

  The local :func:`_run_select` erases line-by-line with ``\\x1b[2K`` and is
  robust to any cursor state.  Call :func:`restore_stdin_terminal` before
  entering the menu so investigation progress UI (Tab watcher) does not
  leave stdin in no-echo mode.  The REPL path keeps :func:`repl_choose_one`
  inside prompt_toolkit's stdout patch context.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import TYPE_CHECKING

from surfaces.shared.terminal.components.key_reader import (
    flush_stdin_unix,
    read_key_unix,
    read_key_windows,
    restore_stdin_terminal,
)

if TYPE_CHECKING:
    from rich.console import Console

# Labels mirror the Slack feedback block in utils/slack_delivery.py.
_CHOICES: list[tuple[str, str]] = [
    ("accurate", "Accurate — root cause identified correctly"),
    ("partial", "Partially accurate — missed some issues"),
    ("inaccurate", "Inaccurate — wrong root cause"),
    ("skip", "Skip for now"),
    ("never", "Never ask again"),
]

_SKIP_KEYS = (b"s", b"S")

# Theme-independent ANSI attributes. The accent colour is read from the active
# theme at render time in ``_run_select`` so it tracks theme changes.
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_HINT = f"  {_DIM}↑↓ / j k  ·  Enter  ·  Esc / s to skip{_RESET}"


def _write_raw(text: str) -> None:
    """Write the console-less (CLI/REPL) feedback text in one TTY-safe call.

    Normalises bare ``\\n`` to ``\\r\\n`` when stdout is a TTY so the context,
    header, note and confirmation lines do not staircase under the REPL's
    ``patch_stdout(raw=True)`` proxy, which passes raw-mode output through
    verbatim. ``_run_select`` already emits ``\\r\\n`` for the menu rows; this
    applies the same rule to the surrounding text. For non-TTY stdout
    (piped/captured/tests) the text is written as-is.
    """
    if sys.stdout.isatty():
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    sys.stdout.write(text)
    sys.stdout.flush()


def _run_select(choices: list[tuple[str, str]]) -> str | None:
    """Arrow-key select menu after streaming output.

    Uses per-line ``\\x1b[2K`` (erase line) instead of a block cursor-position
    assumption.  ``restore_stdin_terminal()`` must run before this so the menu
    starts from canonical echo mode rather than the investigation watcher state.

    Returns the selected key string, or None on Esc / Ctrl-C / s.
    """
    from infrastructure.terminal.theme import PROMPT_ACCENT_ANSI

    restore_stdin_terminal()
    labels = [label for _, label in choices]
    n = len(labels)
    total_lines = n + 1  # n choice lines + 1 hint line
    idx = 0
    is_unix = os.name != "nt"

    if is_unix:
        flush_stdin_unix()

    def _out(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _draw(redraw: bool) -> None:
        if redraw:
            _out(f"\x1b[{total_lines}A")
        for i, label in enumerate(labels):
            if i == idx:
                _out(f"\r\x1b[2K{PROMPT_ACCENT_ANSI}  > {label}{_RESET}\r\n")
            else:
                _out(f"\r\x1b[2K{_DIM}    {label}{_RESET}\r\n")
        _out(f"\r\x1b[2K{_HINT}\r\n")

    _draw(False)

    while True:
        key = (
            read_key_unix(also_cancel=_SKIP_KEYS)
            if is_unix
            else read_key_windows(also_cancel=_SKIP_KEYS)
        )

        if key == "enter":
            _out(f"\x1b[{total_lines}A\r\x1b[J")
            return choices[idx][0]

        if key in ("cancel", "eof"):
            _out(f"\x1b[{total_lines}A\r\x1b[J")
            return None

        if key == "up":
            idx = (idx - 1) % n
            _draw(True)
        elif key == "down":
            idx = (idx + 1) % n
            _draw(True)


def _read_note(*, console: Console | None) -> str:
    from infrastructure.terminal.theme import DIM, SECONDARY

    restore_stdin_terminal()
    if console is not None:
        console.print(
            f"[{SECONDARY}]What was wrong or missing? [{DIM}](Enter to skip)[/]:[/] ", end=""
        )
    else:
        _write_raw("\nWhat was wrong or missing? (Enter to skip): ")
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        return input().strip()
    return ""


def _pick_rating(*, console: Console | None) -> str | None:
    """Show the rating prompt; returns key or None on cancel/skip."""
    if console is not None:
        from surfaces.shared.terminal.components.choice_menu import (
            repl_choose_one,
            repl_tty_interactive,
        )

        if not repl_tty_interactive():
            return None
        return repl_choose_one(title="Was this RCA accurate?", choices=_CHOICES)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return _run_select(_CHOICES)


def _pick_taxonomy(*, console: Console | None) -> str | None:
    """Show the miss-taxonomy picker after a partial/inaccurate rating."""
    from core.domain.feedback import taxonomy_choices

    choices = taxonomy_choices()

    if console is not None:
        from surfaces.shared.terminal.components.choice_menu import (
            repl_choose_one,
            repl_tty_interactive,
        )

        if not repl_tty_interactive():
            return None
        return repl_choose_one(title="Where did this miss come from?", choices=choices)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return _run_select(choices)
