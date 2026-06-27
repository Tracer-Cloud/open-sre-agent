"""Tests for :class:`IncrementalJsonlSource` rotation detection (#2023)."""

from __future__ import annotations

from pathlib import Path

from tools.fleet_monitoring.token_sources import IncrementalJsonlSource, _PerPidState


def test_detect_rotation_returns_none_on_transient_stat_error(tmp_path: Path) -> None:
    """A transient stat failure must leave the cached state intact.

    Regression: an earlier version reset ``offset`` to ``0`` whenever
    stat raised, so the next time the file reappeared on the same
    inode the source would re-read every historical byte from 0 and
    inflate the 60 s token window. The fix returns ``None`` so the
    caller keeps the cached offset and only re-cold-starts on
    positive rotation evidence (inode change, size regression, or
    mtime regression).
    """
    missing = tmp_path / "vanished.jsonl"  # never created → stat raises
    cached = _PerPidState(log_path=missing, inode=42, mtime=1.0, offset=100)

    assert IncrementalJsonlSource._detect_rotation(cached) is None


def test_detect_rotation_returns_new_state_on_inode_change(tmp_path: Path) -> None:
    """Same-path delete+recreate must still trigger a cold-start.

    The OSError fix above must not regress the inode-change branch:
    that one is positive evidence of rotation, and the new state
    should seek to the new file's EOF.
    """
    session = tmp_path / "session.jsonl"
    session.write_text("first\n", encoding="utf-8")
    fake_inode = session.stat().st_ino - 1  # any value different from the real one
    cached = _PerPidState(
        log_path=session,
        inode=fake_inode,
        mtime=session.stat().st_mtime,
        offset=0,
    )

    rotated = IncrementalJsonlSource._detect_rotation(cached)
    assert rotated is not None
    # ``offset`` set to the real file size = "seek to EOF" so we
    # never retro-price the content already on disk.
    assert rotated.offset == session.stat().st_size
    assert rotated.inode == session.stat().st_ino


def test_detect_rotation_returns_new_state_on_size_regression(tmp_path: Path) -> None:
    """Inode-reused truncate-and-rewrite must also trigger a cold-start.

    ext4 and APFS happily reuse a freshly-freed inode within the
    same second; without the size-regression signal the inode-change
    branch would miss it. The cached offset is past the new EOF,
    which would otherwise yield negative deltas.
    """
    session = tmp_path / "session.jsonl"
    session.write_text("hi\n", encoding="utf-8")
    stat = session.stat()
    cached = _PerPidState(
        log_path=session,
        inode=stat.st_ino,
        mtime=stat.st_mtime,
        offset=stat.st_size + 1000,  # cached offset > current size
    )

    rotated = IncrementalJsonlSource._detect_rotation(cached)
    assert rotated is not None
    assert rotated.offset == stat.st_size
