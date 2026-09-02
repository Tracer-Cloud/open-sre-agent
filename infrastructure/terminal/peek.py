"""Peek helpers for collapsed tool output in the transcript.

Pure string logic so collapse behaviour is testable without a live TTY: a long
tool result renders as a short head plus a ``… N more lines`` marker. Expansion
(Ctrl+O) can reuse these helpers once a session buffer + keybinding land; this
module owns only the text shaping.
"""

from __future__ import annotations


def build_output_peek(full_text: str, *, max_lines: int = 3) -> tuple[str, int]:
    """Return ``(peek, hidden_line_count)`` for *full_text*.

    The peek is the first *max_lines* lines; ``hidden`` is how many lines were
    dropped (0 when the text already fits, in which case *peek* is the whole
    text). The caller appends :func:`format_expand_marker` when ``hidden > 0``.
    """
    if not full_text.strip():
        return "", 0
    body = full_text.rstrip("\n")
    lines = body.split("\n")
    if len(lines) <= max_lines:
        return body, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def format_expand_marker(hidden: int) -> str:
    """One-line marker for the folded remainder: ``… N more lines``."""
    noun = "line" if hidden == 1 else "lines"
    return f"… {hidden} more {noun}"


__all__ = ["build_output_peek", "format_expand_marker"]
