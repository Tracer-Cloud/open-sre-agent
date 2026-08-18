"""Tolerant working-tree change capture: changed files plus a reviewable diff.

Coding-agent backends and local fixers all answer the same question after a run:
"what did this change?". This module captures it best-effort — any git failure
yields an empty capture instead of an exception, because the caller's run result
(success, summary, error) must survive a broken git environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from integrations.git.errors import GitCommandError
from integrations.git.local import _run_git, changed_paths

_MAX_UNTRACKED_FILES = 50


@dataclass(frozen=True)
class WorktreeChanges:
    """Snapshot of working-tree changes vs HEAD (tracked edits + untracked files)."""

    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    diff_truncated: bool = False


def _git(workspace: str, *args: str) -> tuple[int, str]:
    """Run git tolerantly: any failure (missing git, timeout) reads as no output."""
    try:
        result = _run_git(workspace, *args)
    except GitCommandError:
        return 1, ""
    return result.returncode, result.stdout or ""


def _untracked_diff(workspace: str) -> str:
    """Diff for new (untracked) files, which ``git diff HEAD`` does not include.

    ``git diff HEAD`` only covers tracked paths, so a file the agent *creates*
    would appear in ``changed_files`` with no diff. We list untracked files
    (``-uall`` expands directories into individual files) and render each as an
    added-content diff via ``git diff --no-index``, which never touches the index.
    """
    rc, out = _git(workspace, "status", "--porcelain", "-uall")
    if rc != 0:
        return ""
    untracked = [line[3:].strip() for line in out.splitlines() if line.startswith("??")]
    chunks: list[str] = []
    for path in untracked[:_MAX_UNTRACKED_FILES]:
        if not path:
            continue
        # `git diff --no-index` exits non-zero when the files differ — expected here;
        # we use whatever it wrote to stdout (the added-content diff).
        _, chunk = _git(workspace, "diff", "--no-index", "--no-color", "--", os.devnull, path)
        if chunk:
            chunks.append(chunk)
    return "".join(chunks)


def capture_worktree_changes(workspace: str, *, max_diff_chars: int) -> WorktreeChanges:
    """Capture (changed files, combined diff, truncation flag) for the working tree.

    The diff covers both tracked edits (``git diff HEAD``) and new untracked files
    (rendered as added content), then is truncated to *max_diff_chars*.
    """
    try:
        changed = changed_paths(workspace)
    except GitCommandError:
        return WorktreeChanges()
    _, tracked = _git(workspace, "diff", "HEAD", "--no-color")
    diff = tracked + _untracked_diff(workspace)
    diff_truncated = False
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars]
        diff_truncated = True
    return WorktreeChanges(changed_files=changed, diff=diff, diff_truncated=diff_truncated)


__all__ = ["WorktreeChanges", "capture_worktree_changes"]
