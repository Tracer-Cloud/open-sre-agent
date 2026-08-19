"""Stdout a quiet ``shell_run`` withheld from live printing.

The runner notes every quiet command and buffers nonempty stdout. The shell
sink may paint buffered text only when exactly one quiet command ran and it
produced one chunk (closer suppressed). Multiple quiet commands — even when
only one left text — are probes for a composed answer; never dump them as
the turn's reply. Core never sees this buffer.
"""

from __future__ import annotations

from contextvars import ContextVar

_quiet_shell_runs: ContextVar[int] = ContextVar("quiet_shell_runs", default=0)
_quiet_stdout: ContextVar[tuple[str, ...]] = ContextVar("quiet_shell_stdout", default=())


def note_quiet_shell_run() -> None:
    """Count a quiet ``shell_run`` that produced no buffered stdout."""
    _quiet_shell_runs.set(_quiet_shell_runs.get() + 1)


def buffer_quiet_stdout(text: str) -> None:
    """Count a quiet ``shell_run`` and retain its nonempty stdout."""
    stripped = text.strip()
    if not stripped:
        return
    note_quiet_shell_run()
    _quiet_stdout.set((*_quiet_stdout.get(), stripped))


def clear_quiet_stdout() -> None:
    _quiet_shell_runs.set(0)
    _quiet_stdout.set(())


def take_quiet_stdout() -> str:
    """Drain when exactly one quiet command left exactly one chunk; else drop.

    Always clears run count and buffer. Multi-command turns must not surface
    an intermediate probe just because other quiet commands were outputless.
    """
    runs = _quiet_shell_runs.get()
    chunks = _quiet_stdout.get()
    _quiet_shell_runs.set(0)
    _quiet_stdout.set(())
    if runs != 1 or len(chunks) != 1:
        return ""
    return chunks[0]
