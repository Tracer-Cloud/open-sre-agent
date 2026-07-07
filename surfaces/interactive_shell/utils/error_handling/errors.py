"""CLI rendering for structured OpenSRE errors.

The frontend-agnostic error contract lives in :mod:`platform.common.errors`.
This module adds the CLI presentation layer: a ``click.ClickException``
subclass whose :meth:`show` renders a clean, traceback-free panel via
:func:`render_error`. CLI code raises this subclass so Click's error path
renders it; non-CLI code (tools, integrations) raises the platform base, and
:mod:`cli.__main__` renders that. Catch ``platform.common.errors.OpenSREError``
to handle both.

render_error()
--------------
Catches any exception and displays a clean, terminal-safe error panel without
ever surfacing a raw Python traceback. Format:

  ✗  ExceptionType                       ← ERROR
     message text                        ← TEXT
     path/to/file.py:42 in fn_name      ← DIM
     Run opensre doctor to diagnose      ← SECONDARY hint
"""

from __future__ import annotations

import sys
import typing as t

import click
from rich.console import Console

from platform.common.errors import OpenSREError as _OpenSREError
from platform.terminal.errors import render_error


# ClickException.message is Final in newer Click; the platform base owns ``message``.
class OpenSREError(_OpenSREError, click.ClickException):  # type: ignore[misc]
    """A CLI error that renders with an optional suggestion and docs URL."""

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        docs_url: str | None = None,
        exit_code: int = 1,
    ) -> None:
        # The platform base sets ``message``/``suggestion``/``docs_url``/
        # ``exit_code`` — all Click needs to render and exit, so we don't call
        # ``ClickException.__init__`` and avoid cooperative-MRO ambiguity.
        _OpenSREError.__init__(
            self, message, suggestion=suggestion, docs_url=docs_url, exit_code=exit_code
        )

    def format_message(self) -> str:
        return _OpenSREError.format_message(self)

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
