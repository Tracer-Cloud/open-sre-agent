from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

_live_console: Console | None = None
_active_display: Any | None = None
_completed_footer_snapshot: tuple[str, float, str, str] | None = None
_prompt_suppress_fn: Callable[[], None] | None = None


def set_prompt_suppress_fn(fn: Callable[[], None] | None) -> None:
    """Register (or clear) the callback that hides the REPL prompt spinner."""
    global _prompt_suppress_fn
    _prompt_suppress_fn = fn


def get_prompt_suppress_fn() -> Callable[[], None] | None:
    return _prompt_suppress_fn


def _capture_footer_snapshot(display: Any) -> None:
    """Record the phase footer fields visible when a display stops."""
    global _completed_footer_snapshot
    if display is None:
        return
    t0 = getattr(display, "_t0", None)
    if t0 is None:
        return
    _completed_footer_snapshot = (
        getattr(display, "_current_phase", ""),
        time.monotonic() - t0,
        getattr(display, "_model", ""),
        getattr(display, "_mode", "local"),
    )


def consume_footer_snapshot() -> tuple[str, float, str, str] | None:
    global _completed_footer_snapshot
    snapshot, _completed_footer_snapshot = _completed_footer_snapshot, None
    return snapshot


def _get_console() -> Console:
    """Return the active Live console when running, else a fresh one."""
    return _live_console or Console(highlight=False)


def set_live_console(console: Console | None) -> None:
    global _live_console
    _live_console = console


def unregister_live_console(expected: Console | None) -> None:
    global _live_console
    if expected is not None and _live_console is expected:
        _live_console = None


def set_active_display(display: Any | None) -> None:
    global _active_display
    _active_display = display


def clear_active_display(expected: Any) -> None:
    global _active_display
    if _active_display is expected:
        _active_display = None


def stop_display() -> None:
    """Stop any running live display before printing final report output."""
    if _active_display is not None:
        _active_display.stop()
    from app.cli.interactive_shell.ui.output.tracker import _stop_active_tracker_toggle_watcher

    _stop_active_tracker_toggle_watcher()
