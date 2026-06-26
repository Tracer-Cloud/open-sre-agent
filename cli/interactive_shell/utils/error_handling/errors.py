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
     path/to/file.py:42 in fn_name      ← DIM
     Run opensre doctor to diagnose      ← SECONDARY hint

Example rendered output (colour roles):
  ┌──────────────────────────────────────────────────────┐ [DIM]
  │  ✗  ValueError                                       │ [ERROR glyph + type]
  │     argument must be positive                        │ [TEXT message]
  │     app/nodes/plan_actions/node.py:88 in _build      │ [DIM location]
  │     Run opensre doctor to diagnose connection issues  │ [SECONDARY hint]
  └──────────────────────────────────────────────────────┘ [DIM]
"""

from __future__ import annotations

import sys
import typing as t

import click
from rich.console import Console

from cli.interactive_shell.ui.errors import render_error


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
