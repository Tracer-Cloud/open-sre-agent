"""Shared error contract for the GitHub write paths.

Creating a branch and opening a pull request are two modules
(:mod:`integrations.github.branches`, :mod:`integrations.github.pull_requests`)
but one operation from a caller's point of view, and a caller wants to catch
both with a single ``except`` and report a stable category rather than a stack
trace. The base class lives in this leaf so neither module has to import the
other.
"""

from __future__ import annotations


class GitHubWriteError(Exception):
    """Expected GitHub write failure carrying a stable ``kind`` for callers.

    ``kind`` is a short snake_case category (``branch_exists``, ``pr_failed``)
    that callers may branch on and surface to a user; ``message`` is the
    human-readable explanation.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


__all__ = ["GitHubWriteError"]
