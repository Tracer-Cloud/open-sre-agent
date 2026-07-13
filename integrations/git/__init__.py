"""Local git client: vendor-neutral branch/commit/push/status helpers.

Public surface for callers that need safe local git operations (e.g. shipping a
code change as a branch + commit + push). Operations raise :class:`GitCommandError`
with a stable ``kind`` that callers map onto their own error model.
"""

from __future__ import annotations

from integrations.git.errors import (
    BRANCH_FAILED,
    COMMIT_FAILED,
    GIT_UNAVAILABLE,
    NOT_A_GIT_REPO,
    PROTECTED_BRANCH,
    PUSH_FAILED,
    GitCommandError,
)
from integrations.git.local import (
    assert_not_protected,
    changed_paths,
    checkout_branch,
    commit_paths,
    create_branch,
    current_branch,
    default_branch,
    ensure_git_repo,
    file_fingerprints,
    is_git_repo,
    push_branch,
    short_head,
)

__all__ = [
    "BRANCH_FAILED",
    "COMMIT_FAILED",
    "GIT_UNAVAILABLE",
    "NOT_A_GIT_REPO",
    "PROTECTED_BRANCH",
    "PUSH_FAILED",
    "GitCommandError",
    "assert_not_protected",
    "changed_paths",
    "checkout_branch",
    "commit_paths",
    "create_branch",
    "current_branch",
    "default_branch",
    "ensure_git_repo",
    "file_fingerprints",
    "is_git_repo",
    "push_branch",
    "short_head",
]
