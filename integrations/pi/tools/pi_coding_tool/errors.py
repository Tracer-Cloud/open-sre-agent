"""Error model for the Pi coding tool.

A single typed exception (:class:`PiCodingError`) carries a stable ``kind`` that is
surfaced to callers as the tool's ``error_kind`` output field, so failures are
machine-classifiable instead of free-text-only.
"""

from __future__ import annotations

# Stable failure categories surfaced in the tool's ``error_kind`` output field.
ERR_DISABLED = "disabled"
ERR_INVALID_INPUT = "invalid_input"
ERR_CLI_UNAVAILABLE = "cli_unavailable"
ERR_TIMEOUT = "timeout"
ERR_EXECUTION = "execution_error"


class PiCodingError(Exception):
    """An expected, user-actionable failure with a stable ``kind``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
