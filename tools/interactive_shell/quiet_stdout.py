"""Stdout a quiet ``shell_run`` withheld from live printing.

The runner buffers the text. The shell sink may paint it when a *single*
quiet command produced the only turn output (closer suppressed). Multiple
buffered chunks are quiet probes for a composed answer — never dump them
as the turn's reply. Core never sees this buffer.
"""

from __future__ import annotations

from contextvars import ContextVar

_quiet_stdout: ContextVar[tuple[str, ...]] = ContextVar("quiet_shell_stdout", default=())


def buffer_quiet_stdout(text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return
    _quiet_stdout.set((*_quiet_stdout.get(), stripped))


def clear_quiet_stdout() -> None:
    _quiet_stdout.set(())


def take_quiet_stdout() -> str:
    """Drain the buffer. Empty when there is nothing, or more than one chunk.

    Multi-chunk buffers are intermediate probes; callers must not join them
    into a user-visible answer. Always clears.
    """
    chunks = _quiet_stdout.get()
    _quiet_stdout.set(())
    if len(chunks) != 1:
        return ""
    return chunks[0]
