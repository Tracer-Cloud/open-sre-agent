"""Rich console adapter for REPL dispatch streaming and cancellation."""

from __future__ import annotations

import sys
import threading
from typing import Any, Protocol

from rich.console import Console
from rich.file_proxy import FileProxy


class _PromptSpinner(Protocol):
    bytes_in: int
    streaming: bool

    def stop(self) -> None:
        raise NotImplementedError


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    def __init__(
        self,
        spinner: _PromptSpinner,
        cancel_event: threading.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Reset the TTY column before each print when not streaming."""
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


__all__ = ["StreamingConsole"]
