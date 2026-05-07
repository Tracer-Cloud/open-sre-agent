"""Structured CLI error with optional suggestion and docs URL.

Follows the pattern from `clig.dev <https://clig.dev/>`_ and flyctl's
error system: every user-facing error can carry a human-readable
suggestion (what to do next) and a docs link.

render_error()
--------------
Catches any exception and displays a clean, terminal-safe error panel
without ever surfacing a raw Python traceback. Format:

  ✗  ExceptionType                       ← ERROR
     message text                        ← TEXT
     path/to/file.py:42 in fn_name      ← ACCENT_DIM
     Run opensre doctor to diagnose      ← TEXT_DIM hint

Example rendered output (colour roles):
  ┌──────────────────────────────────────────────────────┐ [BORDER]
  │  ✗  ValueError                                       │ [ERROR glyph + type]
  │     argument must be positive                        │ [TEXT message]
  │     app/nodes/plan_actions/node.py:88 in _build      │ [ACCENT_DIM location]
  │     Run opensre doctor to diagnose connection issues  │ [TEXT_DIM hint]
  └──────────────────────────────────────────────────────┘ [BORDER]
"""

from __future__ import annotations

import sys
import traceback
import typing as t

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text


class OpenSREError(click.ClickException):
    """A CLI error that renders with an optional suggestion and docs URL."""

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        docs_url: str | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.suggestion = suggestion
        self.docs_url = docs_url
        self.exit_code = exit_code

    def format_message(self) -> str:
        parts = [self.message]
        if self.suggestion:
            parts.append(f"\nSuggestion: {self.suggestion}")
        if self.docs_url:
            parts.append(f"Docs: {self.docs_url}")
        return "\n".join(parts)

    def show(self, file: t.IO[t.Any] | None = None) -> None:
        _file = file if file is not None else sys.stderr
        console = Console(stderr=(_file is sys.stderr), highlight=False)
        # Prefer the structured suggestion over the generic doctor hint.
        custom_hint: str | None = None
        if self.suggestion:
            parts = [self.suggestion]
            if self.docs_url:
                parts.append(f"Docs: {self.docs_url}")
            custom_hint = "  ".join(parts)
        render_error(self, console=console, hint=custom_hint)


def render_error(
    exc: BaseException,
    *,
    console: Console | None = None,
    hint: str | None = None,
) -> None:
    """Display a clean, user-facing error — never a raw traceback.

    Parameters
    ----------
    exc:
        Any exception.  Only the type name, message, and innermost frame
        are shown — never the full stack.
    console:
        Rich Console to print to.  Defaults to stderr.
    hint:
        Override the default "run opensre doctor" hint with a custom line.

    Rendered output (colour roles):
      ✗  ValueError                             ← GLYPH_ERROR + type name in ERROR
         argument must be positive              ← TEXT (message)
         app/nodes/plan.py:88 in _build        ← ACCENT_DIM (file:line in fn)
         Run `opensre doctor` to diagnose       ← TEXT_DIM (hint)
    """
    # Lazy import avoids circular dependency: errors ← interactive_shell ← errors.
    from app.cli.interactive_shell.theme import (
        ACCENT_DIM,
        BORDER,
        ERROR,
        GLYPH_ERROR,
        TEXT,
        TEXT_DIM,
    )

    _console = console or Console(stderr=True, highlight=False)

    exc_type = type(exc).__name__
    exc_msg = str(exc).strip() or "(no detail)"

    # Extract the innermost frame from the traceback (or the current exc info).
    frame_line = ""
    tb = exc.__traceback__
    if tb is not None:
        frames = traceback.extract_tb(tb)
        if frames:
            frame = frames[-1]
            # Make path relative to cwd when possible.
            try:
                path = str(frame.filename)
                from pathlib import Path

                path = str(Path(path).relative_to(Path.cwd()))
            except ValueError:
                path = frame.filename
            frame_line = f"{path}:{frame.lineno} in {frame.name}"

    _hint = hint or "Run opensre doctor to diagnose environment issues."

    body = Text()

    # ✗  ExceptionType
    body.append(f"  {GLYPH_ERROR}  ", style=f"bold {ERROR}")
    body.append(exc_type, style=f"bold {ERROR}")
    body.append("\n")

    # message
    body.append(f"     {exc_msg}", style=TEXT)
    body.append("\n")

    # file:line in fn_name
    if frame_line:
        body.append(f"     {frame_line}", style=ACCENT_DIM)
        body.append("\n")

    # hint
    body.append(f"     {_hint}", style=TEXT_DIM)

    _console.print()
    _console.print(Panel(body, border_style=BORDER, padding=(0, 1), expand=False))
    _console.print()
