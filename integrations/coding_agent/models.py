"""Agent-neutral result of a coding-agent run over a workspace."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CodingResult:
    """Outcome of running a coding agent against a workspace.

    Backend-agnostic: whatever agent produced it (Pi today; others later), callers
    read the same shape — a summary, the files it changed, and the git diff.
    """

    success: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    diff_truncated: bool = False
    returncode: int = 0
    timed_out: bool = False
    error: str | None = None
