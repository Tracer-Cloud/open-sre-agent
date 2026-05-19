"""Deterministic slash fast-path routing phase package."""

from __future__ import annotations

from app.cli.interactive_shell.routing.fast_path.evaluator import fast_path, slash_fast_path

__all__ = [
    "fast_path",
    "slash_fast_path",
]
