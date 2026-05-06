"""Execution tiers for interactive REPL policy (imported without loading the full command registry)."""

from __future__ import annotations

from enum import StrEnum


class ExecutionTier(StrEnum):
    """How aggressively the execution policy gate treats a slash command."""

    EXEMPT = "exempt"
    """Meta commands that must not prompt (e.g. /trust, /exit)."""

    SAFE = "safe"
    """Read-only or informational; always allowed without confirmation."""

    ELEVATED = "elevated"
    """Mutating, expensive, or stops work; may require confirmation."""


__all__ = ["ExecutionTier"]
