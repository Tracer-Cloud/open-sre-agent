"""Typed Hermes operational outcomes and exceptions.

This module centralises stable enums used by Hermes internals so state
transitions are explicit and less stringly-typed.
"""

from __future__ import annotations

from enum import StrEnum


class InvestigationOutcome(StrEnum):
    """Outcome states for Hermes investigation bridge execution."""

    NOT_ATTEMPTED = "not_attempted"
    SUCCESS = "success"
    EMPTY = "empty"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SINK_CLOSED = "sink_closed"


__all__ = ["InvestigationOutcome"]
