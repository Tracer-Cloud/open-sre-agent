"""Rich console adapter for REPL dispatch streaming and cancellation."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.file_proxy import FileProxy

from app.cli.interactive_shell.runtime.state import SpinnerState


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    def __init__(
        self,
        spinner: SpinnerState,
        cancel_event: threading.Event,
        *,
        prompt_invalidator: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event
        self._prompt_invalidator = prompt_invalidator

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def suppress_prompt_spinner(self) -> None:
        """Stop the REPL spinner before another live renderer owns the footer."""
        if not self._spinner.streaming:
            return
        self._spinner.stop()
        if self._prompt_invalidator is not None:
            self._prompt_invalidator()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Reset the TTY column before each print when not streaming."""
        if not self._spinner.streaming and not isinstance(sys.stdout, FileProxy):
            from app.cli.interactive_shell.ui.choice_menu import (
                ensure_tty_column_zero,
                prepare_repl_output_line,
            )
            from app.cli.interactive_shell.ui.rendering import (
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


__all__ = ["StreamingConsole"]
