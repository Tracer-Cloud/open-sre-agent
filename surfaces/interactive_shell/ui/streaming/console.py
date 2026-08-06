"""Rich console adapter for REPL dispatch streaming and cancellation."""

from __future__ import annotations

import sys
import threading
from typing import IO, Any, Protocol

from rich.console import Console, ThemeContext
from rich.file_proxy import FileProxy
from rich.theme import Theme


class _PromptSpinner(Protocol):
    bytes_in: int
    streaming: bool

    def stop(self) -> None:
        """Stop the prompt spinner."""


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    #: Set before ``Console.__init__`` runs, which reaches the ``file`` property.
    _output: Console | None = None

    def __init__(
        self,
        spinner: _PromptSpinner,
        cancel_event: threading.Event,
        *,
        output: Console | None = None,
        **kwargs: Any,
    ) -> None:
        """Stream one turn, writing through ``output`` when a caller supplies it.

        ``capture()`` and ``record`` buffer inside the ``Console`` instance
        rather than at its file, so writing to a copy of the file leaves a
        caller's capture empty. Rendering through the console object itself is
        what makes those see the turn.

        When ``output`` is set, REPL TTY column prep is skipped — the caller's
        stream is not the interactive prompt TTY.
        """
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event
        self._output = output

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Reset the TTY column before each print when not streaming."""
        if self._output is not None:
            self._output.print(*args, **kwargs)
            return
        if not self._spinner.streaming and not isinstance(sys.stdout, FileProxy):
            from surfaces.interactive_shell.ui.components.choice_menu import (
                ensure_tty_column_zero,
                prepare_repl_output_line,
            )
            from surfaces.interactive_shell.ui.components.rendering import (
                _repl_output_already_prepared,
                _repl_table_width,
            )

            if not args and not kwargs:
                ensure_tty_column_zero()
            elif not _repl_output_already_prepared():
                prepare_repl_output_line()
            if sys.stdout.isatty() and "width" not in kwargs:
                kwargs["width"] = _repl_table_width(self)
        super().print(*args, **kwargs)

    def use_theme(self, theme: Theme, *, inherit: bool = True) -> ThemeContext:
        """Push ``theme`` onto the console this turn renders through."""
        if self._output is not None:
            return self._output.use_theme(theme, inherit=inherit)
        return super().use_theme(theme, inherit=inherit)

    @property
    def file(self) -> IO[str]:
        """Track the target console's current file rather than a snapshot."""
        if self._output is not None:
            return self._output.file
        return super().file

    @file.setter
    def file(self, new_file: IO[str]) -> None:
        if self._output is not None:
            self._output.file = new_file
            return
        self._file = new_file


__all__ = ["StreamingConsole"]
