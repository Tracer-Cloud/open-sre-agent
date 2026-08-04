"""Tests for tolerant working-tree change capture."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from integrations.git import capture_worktree_changes


def _git_init_repo(repo: Path) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        env=env,
    )


def test_capture_includes_new_untracked_files(tmp_path: Path) -> None:
    """A file the agent creates (untracked) must appear in the diff, not just
    changed_files. Uses a real git repo since this exercises ``git diff
    --no-index`` behavior."""
    _git_init_repo(tmp_path)
    (tmp_path / "added.py").write_text("print('brand new file')\n", encoding="utf-8")

    changes = capture_worktree_changes(str(tmp_path), max_diff_chars=20000)
    assert "added.py" in changes.changed_files
    assert "added.py" in changes.diff
    assert "brand new file" in changes.diff  # the new file's content is in the diff
    assert changes.diff_truncated is False


def test_capture_includes_tracked_edits(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)
    (tmp_path / "hello.txt").write_text("hello\nedited\n", encoding="utf-8")

    changes = capture_worktree_changes(str(tmp_path), max_diff_chars=20000)
    assert "hello.txt" in changes.changed_files
    assert "+edited" in changes.diff


def test_capture_truncates_long_diffs(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)
    (tmp_path / "hello.txt").write_text("x" * 5000 + "\n", encoding="utf-8")

    changes = capture_worktree_changes(str(tmp_path), max_diff_chars=100)
    assert changes.diff_truncated is True
    assert len(changes.diff) == 100


def test_capture_on_non_repo_is_empty(tmp_path: Path) -> None:
    changes = capture_worktree_changes(str(tmp_path), max_diff_chars=100)
    assert changes.changed_files == []
    assert changes.diff == ""
