"""Shared human-readable duration formatting."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Render a duration as ``12s`` or ``4m 14s`` — never a bare seconds count."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    return f"{whole // 60}m {whole % 60:02d}s"
