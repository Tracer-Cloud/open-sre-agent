"""Stdout a quiet ``shell_run`` withheld from live printing.

The runner buffers the text. The shell sink paints it only when the turn
would otherwise show nothing. Core never sees this buffer.
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
    text = "\n".join(_quiet_stdout.get())
    _quiet_stdout.set(())
    return text
