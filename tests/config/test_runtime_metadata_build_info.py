"""Tests for git build-marker reading across checkout layouts."""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import zlib
from pathlib import Path

from config.runtime_metadata.build_info import (
    GitLayout,
    read_git_head_commit_date,
    read_git_head_full_sha,
    read_git_head_sha,
    read_latest_release_tag,
    resolve_gitdir,
)

_DETACHED_HEAD_SHA = "abcdef0123456789abcdef0123456789abcdef01"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _commit_repo(repo: Path, *, committed: str) -> GitLayout:
    """Init *repo*, make one commit with a fixed committer date, return its layout."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@x",
        "GIT_AUTHOR_DATE": committed,
        "GIT_COMMITTER_DATE": committed,
    }
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("hi", encoding="utf-8")
    _git(repo, "add", "f.txt", env=env)
    _git(repo, "commit", "-q", "-m", "c", env=env)
    return GitLayout(gitdir=repo / ".git", commondir=repo / ".git")


def test_resolve_gitdir_follows_linked_worktree_pointer_file(tmp_path: Path) -> None:
    """Linked worktrees (and submodules) store ``.git`` as a *file* that points
    at the real gitdir under the primary repo. Build metadata must resolve
    through it instead of returning ``None``."""
    real_gitdir = tmp_path / "primary" / ".git" / "worktrees" / "wt1"
    real_gitdir.mkdir(parents=True)
    pointer = tmp_path / "wt" / ".git"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"gitdir: {real_gitdir}\n", encoding="utf-8")
    assert resolve_gitdir(pointer) == real_gitdir


def test_resolve_gitdir_returns_none_for_pointer_to_missing_dir(tmp_path: Path) -> None:
    pointer = tmp_path / ".git"
    pointer.write_text("gitdir: /does/not/exist\n", encoding="utf-8")
    assert resolve_gitdir(pointer) is None


def test_latest_release_tag_reads_packed_refs_when_loose_missing(tmp_path: Path) -> None:
    """After ``git pack-refs`` there is no ``refs/tags/<name>`` file — the tag
    lives only in ``packed-refs``. Build metadata must fall back so packed
    repos still surface a build marker."""
    (tmp_path / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted \n"
        "abc1234abc1234abc1234abc1234abc1234abcd refs/tags/v0.1.2026.7.11\n"
        "def5678def5678def5678def5678def5678def56 refs/heads/main\n",
        encoding="utf-8",
    )
    assert read_latest_release_tag(tmp_path) == "v0.1.2026.7.11"


def test_head_sha_reads_packed_refs_when_loose_ref_missing(tmp_path: Path) -> None:
    """A packed branch has no loose ``refs/heads/<name>`` file; the sha is in
    ``packed-refs``. Falling through instead of following packed-refs would
    drop the SHA from the build marker."""
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "packed-refs").write_text(
        "abc1234abc1234abc1234abc1234abc1234abcd refs/heads/main\n",
        encoding="utf-8",
    )
    layout = GitLayout(gitdir=tmp_path, commondir=tmp_path)
    assert read_git_head_sha(layout) == "abc1234"


def test_head_sha_resolves_branch_from_commondir_in_linked_worktree(tmp_path: Path) -> None:
    """In a linked worktree ``HEAD`` sits in the per-worktree gitdir but the
    branch ref lives in the shared commondir. Reading only the per-worktree
    gitdir would miss the sha and drop it from the build marker."""
    commondir = tmp_path / "primary" / ".git"
    (commondir / "refs" / "heads").mkdir(parents=True)
    (commondir / "refs" / "heads" / "main").write_text(
        "abc1234abc1234abc1234abc1234abc1234abcd\n", encoding="utf-8"
    )
    per_worktree = commondir / "worktrees" / "wt1"
    per_worktree.mkdir(parents=True)
    (per_worktree / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    layout = GitLayout(gitdir=per_worktree, commondir=commondir)
    assert read_git_head_sha(layout) == "abc1234"


def test_latest_release_tag_reads_from_commondir_in_linked_worktree(tmp_path: Path) -> None:
    """Tags are a shared ref: only the commondir's ``refs/tags/`` sees them.
    A worktree-local read would return ``None`` and drop the tag from the
    build marker."""
    commondir = tmp_path / "primary" / ".git"
    tags_dir = commondir / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    (tags_dir / "v0.1.2026.7.11").write_text("sha\n", encoding="utf-8")

    assert read_latest_release_tag(commondir) == "v0.1.2026.7.11"


def test_head_commit_date_reads_committer_timestamp_from_loose_object(tmp_path: Path) -> None:
    """The dev build date is the commit's own date (reproducible per commit),
    read from the committer line of the zlib-deflated loose commit object."""
    committed = _dt.datetime(2026, 8, 27, 15, 30, tzinfo=_dt.UTC)
    body = (
        b"commit 0\x00tree 0\n"
        b"author A <a@x> 1000 +0000\n"
        b"committer C <c@x> " + str(int(committed.timestamp())).encode() + b" +0000\n"
        b"\ncommit message\n"
    )
    (tmp_path / "HEAD").write_text(f"{_DETACHED_HEAD_SHA}\n", encoding="utf-8")
    obj_dir = tmp_path / "objects" / _DETACHED_HEAD_SHA[:2]
    obj_dir.mkdir(parents=True)
    (obj_dir / _DETACHED_HEAD_SHA[2:]).write_bytes(zlib.compress(body))

    result = read_git_head_commit_date(GitLayout(gitdir=tmp_path, commondir=tmp_path))

    assert result is not None
    assert result.date() == _dt.date(2026, 8, 27)


def test_head_commit_date_is_storage_independent_across_git_gc(tmp_path: Path) -> None:
    """The build identity must not change when git packs the HEAD object. The
    committer date reads the same before and after repacking (loose → packed)."""
    layout = _commit_repo(tmp_path / "repo", committed="2026-08-27T12:00:00 +0000")

    loose_date = read_git_head_commit_date(layout)

    _git(tmp_path / "repo", "repack", "-ad", "-q")  # pack all objects, drop loose
    full_sha = read_git_head_full_sha(layout)
    assert full_sha is not None
    loose_path = layout.commondir / "objects" / full_sha[:2] / full_sha[2:]
    assert not loose_path.exists()  # the packed reader is now what is under test

    packed_date = read_git_head_commit_date(layout)

    assert loose_date is not None
    assert loose_date.date() == _dt.date(2026, 8, 27)
    assert packed_date == loose_date


def test_head_commit_date_is_none_when_object_is_absent(tmp_path: Path) -> None:
    """A HEAD sha with no loose object and no pack is unreadable; the reader
    returns None so the version falls back rather than guessing a date."""
    (tmp_path / "HEAD").write_text(f"{_DETACHED_HEAD_SHA}\n", encoding="utf-8")

    result = read_git_head_commit_date(GitLayout(gitdir=tmp_path, commondir=tmp_path))

    assert result is None


def test_latest_release_tag_sorts_numerically_not_lexicographically(tmp_path: Path) -> None:
    """``v0.1.YYYY.M.D`` uses non-padded month/day, so a lexicographic sort
    would pick ``v0.1.2026.9.30`` over the later ``v0.1.2026.10.1`` (because
    ``'9' > '1'`` as ASCII). Regression guard: numeric tuple sort."""
    tags_dir = tmp_path / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    for name in ("v0.1.2026.9.30", "v0.1.2026.10.1", "v0.1.2026.7.11"):
        (tags_dir / name).write_text("sha\n", encoding="utf-8")
    assert read_latest_release_tag(tmp_path) == "v0.1.2026.10.1"
