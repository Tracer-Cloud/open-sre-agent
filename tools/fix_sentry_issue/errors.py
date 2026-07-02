"""Error model for the Sentry issue-fix tool."""

from __future__ import annotations

# Stable failure categories surfaced in the tool's ``error_kind`` output field.
ERR_DISABLED = "disabled"
ERR_INVALID_INPUT = "invalid_input"
ERR_SENTRY_UNAVAILABLE = "sentry_unavailable"
ERR_ISSUE_NOT_FOUND = "issue_not_found"
ERR_CLI_UNAVAILABLE = "cli_unavailable"
ERR_TIMEOUT = "timeout"
ERR_EXECUTION = "execution_error"


class FixIssueError(Exception):
    """An expected, user-actionable failure with a stable ``kind``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
