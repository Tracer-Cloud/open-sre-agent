"""Polling-based file tailer for Hermes log files.

Behavior intentionally mirrors ``tail -F`` (capital-F) semantics:

* On open, seek to a configurable position (default: end-of-file, so live
  tailing only reports *new* lines).
* On each poll cycle, read everything available and split into complete
  lines. A trailing partial line (no newline yet) is buffered and prepended
  to the next cycle so we never emit half a record.
* Detect file rotation by inode/device change *or* truncation (file size
  smaller than our last position). On either, reopen from offset 0 so we
  do not miss the first lines written to the rotated-in file.
* The tailer does not parse content. It yields raw line strings (with
  trailing newlines stripped). Parsing happens in :mod:`app.hermes.parser`.

The tailer is single-threaded and pull-based (``__iter__`` blocks on
``poll_interval_s``); :class:`app.hermes.agent.HermesAgent` runs it on its
own daemon thread and dispatches lines through the parser/classifier.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S: Final[float] = 0.5
DEFAULT_READ_CHUNK: Final[int] = 64 * 1024
_REOPEN_LOG_THROTTLE_S: Final[float] = 30.0


@dataclass(frozen=True)
class _FileFingerprint:
    """Identifier for the *physical* file currently backing the path.

    Comparing ``(device, inode)`` across polls lets us notice a rename/
    swap (logrotate) even when the path itself is unchanged.
    """

    device: int
    inode: int


class FileTailer:
    """Polling tailer that follows a logfile across rotations.

    Construct with ``from_start=True`` to replay the existing file from
    offset 0 (useful for tests and one-shot scans). The default
    ``from_start=False`` matches ``tail -F``: only new lines after the
    tailer attaches are emitted.
    """

    __slots__ = (
        "_path",
        "_poll_interval_s",
        "_read_chunk",
        "_from_start",
        "_stop_event",
        "_partial",
        "_pending_lines",
        "_fingerprint",
        "_position",
        "_last_reopen_log",
    )

    def __init__(
        self,
        path: Path | str,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        read_chunk: int = DEFAULT_READ_CHUNK,
        from_start: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        if read_chunk <= 0:
            raise ValueError("read_chunk must be > 0")

        self._path = Path(path)
        self._poll_interval_s = poll_interval_s
        self._read_chunk = read_chunk
        self._from_start = from_start
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._partial: str = ""
        self._pending_lines: deque[str] = deque()
        self._fingerprint: _FileFingerprint | None = None
        self._position: int = 0
        self._last_reopen_log: float = 0.0

    @property
    def path(self) -> Path:
        return self._path

    def stop(self) -> None:
        """Signal :meth:`__iter__` to exit at the next sleep boundary."""
        self._stop_event.set()

    def __iter__(self) -> Iterator[str]:
        while True:
            yield from self._drain_once()
            # ``stop()`` should not discard lines that are already buffered in
            # ``_pending_lines`` (e.g. stop signal arrives mid-drain).
            if self._stop_event.is_set() and not self._pending_lines:
                break
            if self._stop_event.is_set():
                continue
            self._stop_event.wait(self._poll_interval_s)

    def _drain_once(self) -> Iterator[str]:
        while self._pending_lines:
            yield self._pending_lines.popleft()
        try:
            stat = os.stat(self._path)
        except FileNotFoundError:
            self._fingerprint = None
            self._position = 0
            self._partial = ""
            self._pending_lines.clear()
            return
        except OSError as exc:
            self._maybe_log_reopen("cannot stat %s: %s", self._path, exc)
            return

        fingerprint = _FileFingerprint(device=stat.st_dev, inode=stat.st_ino)
        rotated = self._fingerprint is not None and fingerprint != self._fingerprint
        truncated = self._fingerprint is not None and stat.st_size < self._position

        if self._fingerprint is None:
            # First attach: ``from_start`` chooses replay vs live-tail.
            self._position = 0 if self._from_start else stat.st_size
            self._partial = ""
            self._pending_lines.clear()
            self._fingerprint = fingerprint
        elif rotated or truncated:
            self._maybe_log_reopen(
                "log file %s %s; reopening from offset 0",
                self._path,
                "rotated" if rotated else "truncated",
            )
            self._position = 0
            self._partial = ""
            self._pending_lines.clear()
            self._fingerprint = fingerprint

        if stat.st_size <= self._position:
            return

        try:
            yield from self._read_from_current_position()
        except OSError as exc:
            self._maybe_log_reopen("read failed on %s: %s", self._path, exc)

    def _read_from_current_position(self) -> Iterator[str]:
        """Read complete lines from the file handle.

        File bytes for each chunk are committed to ``_position`` and
        ``_partial`` *before* complete lines are yielded. Any lines not yet
        yielded after a chunk is parsed live in ``_pending_lines`` so a
        consumer that stops mid-iteration (``GeneratorExit``) does not skip
        trailing lines in the chunk — the next drain cycle drains the queue first.
        """
        with open(self._path, encoding="utf-8", errors="replace") as fh:
            fh.seek(self._position)
            while True:
                while self._pending_lines:
                    yield self._pending_lines.popleft()
                chunk = fh.read(self._read_chunk)
                if not chunk:
                    break
                chunk_end = fh.tell()
                data = self._partial + chunk
                parts = data.splitlines(keepends=True)
                if not parts:
                    self._partial = ""
                    self._position = chunk_end
                    continue
                if parts[-1].endswith(("\n", "\r")):
                    self._partial = ""
                    complete_parts = parts
                else:
                    self._partial = parts[-1]
                    complete_parts = parts[:-1]
                completes = [p.rstrip("\r\n") for p in complete_parts]
                self._position = chunk_end
                self._pending_lines.extend(completes)

    def _maybe_log_reopen(self, fmt: str, *args: object) -> None:
        # Throttle noisy reopen/error logs so a long-missing file does not
        # flood structured-logging pipelines once per poll interval.
        now = time.monotonic()
        if now - self._last_reopen_log < _REOPEN_LOG_THROTTLE_S:
            return
        self._last_reopen_log = now
        logger.warning(fmt, *args)


__all__ = [
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_READ_CHUNK",
    "FileTailer",
]
