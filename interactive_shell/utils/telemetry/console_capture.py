"""Capture Rich console output without suppressing on-screen rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console


@contextmanager
def capture_console_segment(console: Console) -> Iterator[Callable[[], str]]:
    """Record console output printed inside the block (tee to the real console).

    Uses Rich's ``record`` mode with ``export_text(clear=False)`` so output still
    appears in the REPL while a plain-text slice is available for analytics.
    """
    was_recording = console.record
    console.record = True
    start = len(console.export_text(clear=False))

    def get_captured() -> str:
        return console.export_text(clear=False)[start:].strip()

    try:
        yield get_captured
    finally:
        console.record = was_recording
