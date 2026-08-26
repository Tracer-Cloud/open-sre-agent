"""Prompt-region line width and clipping (leaf — no shell runtime imports).

Live prompt rows (spinner, idle hint, auto status, overlays) must stay one
terminal line. A glyph in the last column puts the cursor in pending-wrap;
on shrink-resize the emulator soft-wraps and prompt-toolkit row accounting
drifts, leaving stale frames in scrollback.
"""

from __future__ import annotations

from prompt_toolkit.application.current import get_app_or_none

from infrastructure.safety.terminal_output import strip_terminal_controls

DEFAULT_TERMINAL_COLUMNS = 80


def terminal_columns() -> int:
    """Current terminal width, or 80 when no prompt_toolkit app is active."""
    app = get_app_or_none()
    if app is None:
        return DEFAULT_TERMINAL_COLUMNS
    try:
        return app.output.get_size().columns
    except Exception:
        return DEFAULT_TERMINAL_COLUMNS


def prompt_line_width(cols: int | None = None) -> int:
    """Visible width for a live prompt-region line (last column left empty)."""
    width = terminal_columns() if cols is None else cols
    return max(width - 1, 1)


def clip_prompt_text(text: str, max_len: int) -> str:
    """Truncate to ``max_len`` visible characters after stripping controls."""
    visible = strip_terminal_controls(text)
    if max_len <= 0:
        return ""
    if len(visible) <= max_len:
        return visible
    return visible[: max_len - 1] + "…"


__all__ = ["DEFAULT_TERMINAL_COLUMNS", "clip_prompt_text", "prompt_line_width", "terminal_columns"]
