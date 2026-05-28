"""Per-provider streaming sources for the agents dashboard.

A :class:`TokenSource` is the on-disk-log half of the metering
pipeline. The sampler asks each registered agent's source for any
newly emitted bytes once per tick; the result feeds a meter, which
feeds the rate tracker.

Return-value semantics:

- ``None``: this source cannot observe ``pid`` at all. Dashboard renders ``-``.
- ``""``: measurable but no new bytes. Meter returns 0; tracker
  records an idle observation; dashboard renders ``0``.
- non-empty: forward to meter.

:class:`IncrementalJsonlSource` is the abstract base — both real
sources share the same per-PID file-state bookkeeping
(path, inode, mtime, offset), rotation detection, and incremental
binary read. Subclasses only implement ``_resolve`` (provider-
specific path lookup); the base always seeks to EOF on cold-start
so historical content is never retro-priced.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

# Per-tick cap on incremental reads. Bounds the cold-start case where
# a long-running session's log is already several megabytes.
_MAX_BYTES_PER_TICK = 4 * 1024 * 1024


class TokenSource(Protocol):
    """Per-PID streaming reader of newly-emitted CLI output.

    See module docstring for the ``None`` / ``""`` / non-empty
    contract on ``read_new_chunk``.
    """

    def read_new_chunk(self, pid: int, /) -> str | None:
        """Return new output bytes for ``pid``, ``""`` for idle, or ``None`` when unobservable."""
        raise NotImplementedError

    def forget(self, pid: int, /) -> None:
        """Drop all read-position state for ``pid`` without affecting other active processes."""
        raise NotImplementedError

    def known_pids(self) -> list[int]:
        """Return PIDs with retained source state so callers can reconcile stale processes."""
        raise NotImplementedError


class NullTokenSource:
    """A source that observes nothing."""

    def read_new_chunk(self, _pid: int, /) -> str | None:
        return None

    def forget(self, _pid: int, /) -> None:
        return None

    def known_pids(self) -> list[int]:
        return []


null_token_source: TokenSource = NullTokenSource()


@dataclass(frozen=True)
class _PerPidState:
    log_path: Path
    inode: int
    mtime: float
    offset: int


class IncrementalJsonlSource(ABC):
    """Tail an on-disk JSONL file incrementally, per PID.

    Subclasses implement :meth:`_resolve` to locate the active log
    file for a given PID. Everything else — cold-start seek to EOF,
    inode-based rotation detection, the 4 MiB per-tick byte cap,
    encoding fallback — is inherited.
    """

    def __init__(self) -> None:
        self._state: dict[int, _PerPidState] = {}

    def read_new_chunk(self, pid: int) -> str | None:
        state = self._state.get(pid)
        if state is None:
            resolved = self._resolve(pid)
            if resolved is None:
                return None
            self._state[pid] = resolved
            return ""

        rotated = self._detect_rotation(state)
        if rotated is not None:
            self._state[pid] = rotated
            return ""

        return self._read_incremental(pid, state)

    def forget(self, pid: int) -> None:
        self._state.pop(pid, None)

    def known_pids(self) -> list[int]:
        return list(self._state.keys())

    @abstractmethod
    def _resolve(self, pid: int) -> _PerPidState | None:
        """Locate the active log for ``pid``. Return ``None`` if unobservable."""

    @staticmethod
    def _initial_state_for(path: Path) -> _PerPidState | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return _PerPidState(
            log_path=path,
            inode=stat.st_ino,
            mtime=stat.st_mtime,
            offset=stat.st_size,
        )

    @staticmethod
    def _detect_rotation(state: _PerPidState) -> _PerPidState | None:
        """Return a fresh state when the cached file rotated, else ``None``.

        Rotation signals (require positive evidence from a successful
        ``stat``): inode changed (delete+recreate), size regressed
        below the cached offset (filesystems like ext4 and APFS reuse
        recently-freed inodes, so the inode-change signal alone misses
        unlink+rewrite when the new file lands on the same inode), or
        mtime moved backwards by more than 5 s. On any signal, seek to
        the new file's EOF so we never retro-price historical content.

        A transient stat failure (file briefly absent, EAGAIN on a
        busy FS) leaves the cached state intact — flipping ``offset``
        to 0 on every stat error would replay the whole file the next
        time it reappears on the same inode, double-counting every
        token already billed.
        """
        try:
            stat = state.log_path.stat()
        except OSError:
            return None
        if stat.st_ino != state.inode:
            return _PerPidState(
                log_path=state.log_path,
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                offset=stat.st_size,
            )
        if stat.st_size < state.offset:
            return _PerPidState(
                log_path=state.log_path,
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                offset=stat.st_size,
            )
        if stat.st_mtime + 5.0 < state.mtime:
            return _PerPidState(
                log_path=state.log_path,
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                offset=stat.st_size,
            )
        return None

    def _read_incremental(self, pid: int, state: _PerPidState) -> str:
        try:
            with state.log_path.open("rb") as fh:
                fh.seek(state.offset)
                raw = fh.read(_MAX_BYTES_PER_TICK)
                new_offset = fh.tell()
                stat = os.fstat(fh.fileno())
        except OSError:
            return ""
        if not raw:
            return ""
        self._state[pid] = replace(state, offset=new_offset, mtime=stat.st_mtime)
        return raw.decode("utf-8", errors="replace")


def safe_mtime(path: Path) -> float:
    """``path.stat().st_mtime`` with a 0.0 fallback for stat errors."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


__all__ = [
    "IncrementalJsonlSource",
    "NullTokenSource",
    "TokenSource",
    "null_token_source",
    "safe_mtime",
]
