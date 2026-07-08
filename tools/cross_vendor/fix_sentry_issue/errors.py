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

# Shipping (PR-open) failure categories. Only reachable when open_pr is requested.
ERR_SHIP_DISABLED = "ship_disabled"
ERR_GIT_UNAVAILABLE = "git_unavailable"
ERR_NOT_A_GIT_REPO = "not_a_git_repo"
ERR_NO_CHANGES = "no_changes"
ERR_PROTECTED_BRANCH = "protected_branch"
ERR_BRANCH_FAILED = "branch_failed"
ERR_COMMIT_FAILED = "commit_failed"
ERR_PUSH_FAILED = "push_failed"
ERR_GITHUB_TOKEN = "github_token_missing"
ERR_PR_FAILED = "pr_failed"


class FixIssueError(Exception):
    """An expected, user-actionable failure with a stable ``kind``.

    ``branch_name`` is set when the failure happens *after* the fix was committed
    to a fresh branch (e.g. push/PR-creation failures), so callers can point the
    user at the branch to push or open a PR manually.
    """

    def __init__(self, kind: str, message: str, *, branch_name: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.branch_name = branch_name
