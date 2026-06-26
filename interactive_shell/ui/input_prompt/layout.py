"""Shared sizing and clipping helpers for prompt UI text."""

from __future__ import annotations

from prompt_toolkit.application.current import get_app_or_none

_DEFAULT_TERMINAL_COLUMNS = 80
_COMPLETION_META_PADDING = 6
_COMPLETION_META_MIN_WIDTH = 24


def _terminal_columns() -> int:
    app = get_app_or_none()
    if app is None:
        return _DEFAULT_TERMINAL_COLUMNS
    try:
        return app.output.get_size().columns
    except Exception:
        return _DEFAULT_TERMINAL_COLUMNS


def _clip_text(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _completion_meta_width(command_name: str, cols: int) -> int:
    return max(_COMPLETION_META_MIN_WIDTH, cols - len(command_name) - _COMPLETION_META_PADDING)


def _short_meta(
    text: str,
    *,
    command_name: str = "",
    max_len: int | None = None,
    cols: int | None = None,
) -> str:
    if max_len is None:
        if command_name:
            max_len = _completion_meta_width(command_name, cols or _terminal_columns())
        else:
            max_len = 54
    return _clip_text(text, max_len)
