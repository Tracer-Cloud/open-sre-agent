"""Efficient, cursor-based polling primitive for Hermes log files.

This module is the **shared engine** behind both the agent-facing
``HermesLogsTool`` (``app.tools.HermesLogsTool``) and the test helper
(``tests.utils.hermes_logs_helper``). Centralising the cursor logic
here means the production tool and the test suite never drift.

Design goals
------------

* **O(new-lines) per poll** — never re-read the entire log on each
  call. A :class:`HermesLogCursor` records the file's identity (path,
  device, inode) and last byte offset; subsequent polls seek directly
  to the offset and read only what is new.
* **Rotation- and truncation-safe** — every poll re-stat's the file
  and resets the offset if the inode changed (rotation) or the size
  shrank (truncation), so an active poller never silently misses
  lines after ``logrotate`` runs.
* **No daemon thread required** — the agent calls this from its main
  loop and the test helper from a single test thread. For the
  background-polling case the existing ``HermesAgent`` already wraps
  ``FileTailer``; this module is the synchronous primitive both rely
  on.
* **Bounded** — ``max_lines`` caps a single poll so a multi-GB rotated
  file can't blow up the caller's memory.
"""

from __future__ import annotations

import os
import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, LogLevel, LogRecord
from app.hermes.parser import parse_log_line

# Hard upper bound on a single poll's byte read. Hermes errors.log is
# usually <50 MB even on busy installs; 64 MB is a generous ceiling
# that prevents pathological reads if a caller passes max_lines=None.
_DEFAULT_MAX_BYTES: Final[int] = 64 * 1024 * 1024


def _opens_python_traceback(message: str) -> bool:
    """True when *message* starts the standard logging exception header.

    Only such lines are queued for ``since`` inheritance: every other
    non-continuation line updates ``last_parent_passes_since`` but must not
    occupy a FIFO slot, otherwise a filtered pre-``since`` noise line sits
    ahead of a passing Traceback header and the first frame pops the wrong
    decision.
    """
    lower = message.casefold()
    return "traceback" in lower and "most recent call" in lower


@dataclass(frozen=True, slots=True)
class HermesLogCursor:
    """Resumable read position in a Hermes log file.

    The triple ``(path, device, inode)`` identifies the *physical* file
    so a log rotation that replaces ``errors.log`` with a fresh file
    invalidates the cursor and the poller starts from offset 0.

    ``offset`` is the byte position immediately AFTER the last line
    yielded on the previous poll. A poller seeks to this offset, reads,
    and returns a new cursor with an updated offset.

    Cursors are cheap to round-trip through JSON — the agent tool emits
    one in every response so the LLM can pass it back on the next call
    to "tail since last time".
    """

    path: str
    device: int
    inode: int
    offset: int

    @classmethod
    def at_start(cls, path: Path | str) -> HermesLogCursor:
        """Cursor pointing at the very first byte of ``path``.

        Identity fields (device/inode) are zeroed so the first real
        poll will treat any existing file as 'new' and re-stat it.
        """
        return cls(path=str(path), device=0, inode=0, offset=0)

    @classmethod
    def at_end(cls, path: Path | str) -> HermesLogCursor:
        """Cursor pointing at the current end-of-file for ``path``.

        Used by ``opensre hermes watch`` to start a live tail without
        replaying historical lines. ``stat`` failures return an
        at-start cursor so the next poll can recover gracefully.
        """
        p = Path(path)
        try:
            stat = p.stat()
        except (FileNotFoundError, PermissionError):
            return cls.at_start(p)
        return cls(path=str(p), device=stat.st_dev, inode=stat.st_ino, offset=stat.st_size)

    def to_token(self) -> str:
        """Compact opaque token (safe for JSON / LLM round-trip)."""
        return f"{self.device}:{self.inode}:{self.offset}@{self.path}"

    @classmethod
    def from_token(cls, token: str) -> HermesLogCursor:
        """Inverse of :meth:`to_token`. Raises ``ValueError`` on bad input.

        We accept the exact shape we emit; refusing anything else
        prevents a malformed LLM-supplied cursor from silently
        defaulting to ``at_start`` and replaying gigabytes of logs.

        Callers that re-ingest tokens from untrusted context (e.g. an
        LLM echoing text from a log line) must also call
        :meth:`validate_expected_log_path` before opening ``path``.
        """
        match = re.fullmatch(r"(\d+):(\d+):(\d+)@(.+)", token)
        if match is None:
            raise ValueError(f"unrecognised HermesLogCursor token: {token!r}")
        return cls(
            device=int(match.group(1)),
            inode=int(match.group(2)),
            offset=int(match.group(3)),
            path=match.group(4),
        )

    def validate_expected_log_path(self, expected: Path | str) -> None:
        """Ensure ``self.path`` is the same file as ``expected``.

        The token embeds a raw path string; without this check, a
        crafted token could point at an arbitrary filesystem path while
        the tool operator believes they are tailing the configured
        Hermes log.
        """
        try:
            token_resolved = Path(self.path).expanduser().resolve(strict=False)
            want_resolved = Path(expected).expanduser().resolve(strict=False)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError("cannot resolve cursor path or requested log path") from exc
        if token_resolved != want_resolved:
            raise ValueError("cursor token does not refer to the requested log file")


@dataclass(frozen=True, slots=True)
class HermesLogPoll:
    """Result of a single :func:`poll_hermes_logs` invocation."""

    cursor: HermesLogCursor
    records: tuple[LogRecord, ...]
    incidents: tuple[HermesIncident, ...]
    # True when the underlying file changed identity (rotation) or
    # shrank (truncation) since the previous cursor was captured. The
    # poller transparently rewinds in either case; this flag is purely
    # informational for callers that want to log the transition.
    rotation_detected: bool = False
    # Number of lines NOT returned because the read hit ``max_lines`` or
    # the per-poll byte budget stopped before EOF (cursor still before
    # ``stat.st_size``). Callers should re-poll with the returned cursor.
    # ``0`` means everything through EOF was consumed under both caps.
    truncated_lines: int = 0
    # Stats useful to the agent / tests without re-scanning the
    # returned records: how many lines were parsed vs. yielded.
    parsed_line_count: int = field(default=0)

    @property
    def has_new_data(self) -> bool:
        return bool(self.records) or bool(self.incidents) or self.rotation_detected


def poll_hermes_logs(
    log_path: Path | str,
    cursor: HermesLogCursor | None = None,
    *,
    max_lines: int | None = 2000,
    classifier: IncidentClassifier | None = None,
    level_filter: frozenset[LogLevel] | None = None,
    since: datetime | None = None,
) -> HermesLogPoll:
    """Read new lines from a Hermes log file since ``cursor``.

    Parameters
    ----------
    log_path:
        Path to the Hermes log file (typically
        ``~/.hermes/logs/errors.log``).
    cursor:
        Resume position. ``None`` means "start from offset 0" and is
        equivalent to passing ``HermesLogCursor.at_start(log_path)``.
    max_lines:
        Cap on records returned per poll. ``None`` disables the cap.
        When the cap is hit, ``truncated_lines`` reports how many
        records were left behind; the returned cursor still advances
        past the records that were yielded so a follow-up poll picks
        up where we left off.
    classifier:
        Optional :class:`IncidentClassifier`. When provided, every
        parsed record is fed through it and the emitted incidents are
        included in :class:`HermesLogPoll`. Passing the same
        classifier instance across polls preserves traceback buffering
        / warning-burst windows across calls — that's the contract the
        agent and the test helper rely on.
    level_filter:
        Optional set of :class:`LogLevel` values to retain. When set,
        records of other levels are still **observed by the classifier**
        (so traceback continuations and warning bursts still work) but
        are dropped from the returned ``records`` tuple. Defaults to no
        filter.
    since:
        Drop records with ``timestamp < since`` from the returned
        records. As with ``level_filter``, the classifier still
        observes them so cross-poll burst windows remain intact.

    Returns
    -------
    :class:`HermesLogPoll` with the new records, any incidents the
    classifier emitted, an updated cursor, and rotation/truncation
    flags.

    Failure modes
    -------------
    * **Missing file:** returns an empty :class:`HermesLogPoll` with
      an :meth:`HermesLogCursor.at_start` cursor — a follow-up poll
      after the file is created will read from offset 0.
    * **Permission error:** raised — the caller is expected to surface
      this through their normal error path (the tool serialises it
      into a ``{"error": ...}`` response).
    """
    p = Path(log_path)
    resolved_cursor = cursor or HermesLogCursor.at_start(p)
    classifier_local = classifier if classifier is not None else IncidentClassifier()

    try:
        stat = p.stat()
    except FileNotFoundError:
        return HermesLogPoll(
            cursor=HermesLogCursor.at_start(p),
            records=(),
            incidents=(),
            rotation_detected=False,
            truncated_lines=0,
            parsed_line_count=0,
        )

    rotation_detected = _is_rotation_or_truncation(resolved_cursor, stat)
    start_offset = 0 if rotation_detected else min(resolved_cursor.offset, stat.st_size)

    # If nothing new since last poll, short-circuit before opening the
    # file. This is the hot path on idle systems: an agent polling
    # every few seconds against a quiet errors.log should be ~free.
    #
    # We still update the cursor's device/inode from the current stat
    # so a subsequent rotation IS detected — without this, an
    # at_start cursor that hits an empty file would forever appear
    # to be a 'first poll' (device=0, inode=0) and a later rotation
    # would slip through.
    if not rotation_detected and start_offset >= stat.st_size:
        return HermesLogPoll(
            cursor=HermesLogCursor(
                path=str(p), device=stat.st_dev, inode=stat.st_ino, offset=stat.st_size
            ),
            records=(),
            incidents=(),
            rotation_detected=False,
            truncated_lines=0,
            parsed_line_count=0,
        )

    records, incidents, new_offset, parsed_count, truncated = _read_segment(
        p,
        start_offset=start_offset,
        max_lines=max_lines,
        classifier=classifier_local,
        level_filter=level_filter,
        since=since,
    )

    return HermesLogPoll(
        cursor=HermesLogCursor(
            path=str(p), device=stat.st_dev, inode=stat.st_ino, offset=new_offset
        ),
        records=records,
        incidents=incidents,
        rotation_detected=rotation_detected,
        truncated_lines=truncated,
        parsed_line_count=parsed_count,
    )


def _is_rotation_or_truncation(cursor: HermesLogCursor, stat: os.stat_result) -> bool:
    # First-ever poll: device/inode == 0 (sentinel from at_start). We
    # have no prior identity to compare against, so treat as fresh
    # read rather than rotation.
    if cursor.device == 0 and cursor.inode == 0:
        return False
    if cursor.device != stat.st_dev or cursor.inode != stat.st_ino:
        return True
    # File shrank below our last offset → it was truncated; rewind.
    return stat.st_size < cursor.offset


def _read_segment(
    path: Path,
    *,
    start_offset: int,
    max_lines: int | None,
    classifier: IncidentClassifier,
    level_filter: frozenset[LogLevel] | None,
    since: datetime | None,
) -> tuple[tuple[LogRecord, ...], tuple[HermesIncident, ...], int, int, int]:
    """Read [start_offset, EOF) and return (records, incidents, new_offset,
    parsed_count, truncated_lines).
    """
    records: list[LogRecord] = []
    incidents: list[HermesIncident] = []
    parsed_count = 0
    truncated = 0

    # The previous-level latch lets the parser tag traceback
    # continuations with their parent record's severity even when
    # the parent landed in an earlier poll. The classifier already
    # buffers the open traceback for us across calls.
    prev_level: LogLevel | None = None
    # Continuation records carry no logger and inherit datetime.min, so
    # ``since`` filtering must track the last non-continuation record's
    # decision and propagate it to subsequent continuation lines.
    #
    # The tricky case is two loggers interleaving in the file:
    #
    #   t=20s  logger-A: Traceback …   → passes since filter
    #   t=05s  logger-B: unrelated     → filtered by since filter
    #   (continuation frame)           → belongs to logger-A's traceback,
    #                                    must still pass
    #
    # A scalar "parent_passes_since" would be overwritten by logger-B and
    # the continuation would inherit the wrong decision.  Instead we keep a
    # FIFO queue of filter decisions for Traceback **openers** only (see
    # ``_opens_python_traceback``).  Non-traceback headers update
    # ``last_parent_passes_since`` but are not queued, so a filtered line
    # before a passing Traceback does not steal the continuation's decision.
    #
    # Invariant: in real Python logging a traceback is a single log-call, so
    # each header produces exactly one block of consecutive continuations.
    # The FIFO pairing matches physical write order.
    since_queue: deque[bool] = deque()  # one entry per Traceback opener, in order
    prev_was_continuation = False  # tracks boundary for queue pop
    last_parent_passes_since = since is None  # seed when queue is empty
    new_offset = start_offset

    with path.open("rb") as handle:
        handle.seek(start_offset)
        # Cap the maximum bytes we'll read in one call so a runaway
        # log can't OOM us. Consume whole lines only: if the next line
        # cannot fit entirely in ``budget``, seek back before that line so
        # the caller's cursor retries it on the next poll.
        budget = _DEFAULT_MAX_BYTES
        while True:
            line_start = handle.tell()
            raw = handle.readline()
            # ``line_start`` is the resume offset on EOF or when the line
            # does not fit in ``budget``; assign once here so CodeQL does
            # not flag redundant writes on those break paths.
            new_offset = line_start
            if not raw:
                break
            if len(raw) > budget:
                handle.seek(line_start)
                # Budget exhausted before the next full line could be read.
                # Signal truncation when unread bytes remain so callers (e.g.
                # ``get_hermes_logs``) set ``has_more=True`` and re-poll; without
                # this, ``truncated_lines`` stays 0 while ``cursor.offset`` is
                # still before EOF and the agent stops tailing prematurely.
                try:
                    file_size = os.fstat(handle.fileno()).st_size
                except OSError:
                    file_size = line_start
                if file_size > line_start:
                    truncated = max(truncated, 1)
                break
            budget -= len(raw)
            # Post-line offset is deterministic from line_start + raw length;
            # no extra tell() needed and there is exactly one assignment path
            # per iteration that reaches the loop top again.
            line_end = line_start + len(raw)

            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            record = parse_log_line(line, prev_level=prev_level)
            if record is None:
                new_offset = line_end
                continue

            passes_level = level_filter is None or record.level in level_filter
            if since is None:
                passes_since = True
            elif record.is_continuation:
                if not prev_was_continuation and since_queue:
                    # First continuation in a new block: pop the oldest header
                    # entry (= the header that opened this traceback block).
                    # The entry is kept at the front so subsequent lines in the
                    # same block (prev_was_continuation=True) simply peek it.
                    last_parent_passes_since = since_queue.popleft()
                passes_since = last_parent_passes_since
            else:
                passes_since = record.timestamp >= since
                # Push this Traceback opener's decision.  Plain log lines do
                # not open continuation blocks in our format and must not
                # consume FIFO slots ahead of a later Traceback header.
                if _opens_python_traceback(record.message):
                    since_queue.append(passes_since)
                last_parent_passes_since = passes_since
            prev_was_continuation = record.is_continuation
            would_return = passes_level and passes_since
            # If this line would become the (max_lines+1)th returned record,
            # rewind before it without calling observe(). The cursor must
            # retry the same bytes on the next poll; observing here first
            # would duplicate classifier incidents when the next poll uses a
            # fresh classifier (e.g. get_hermes_logs per call).
            if max_lines is not None and len(records) >= max_lines and would_return:
                try:
                    file_size = os.fstat(handle.fileno()).st_size
                except OSError:
                    file_size = line_start
                remaining_bytes = max(0, file_size - line_start)
                consumed_bytes = max(0, line_start - start_offset)
                avg_bytes_per_record = consumed_bytes / max(len(records), 1)
                truncated = max(
                    1,
                    int(remaining_bytes / max(avg_bytes_per_record, 1.0)),
                )
                handle.seek(line_start)
                break

            parsed_count += 1
            if not record.is_continuation:
                prev_level = record.level

            # Classifier always observes the record so traceback
            # buffering / warning-burst windows are correct.
            for incident in classifier.observe(record):
                incidents.append(incident)

            if passes_level and passes_since:
                records.append(record)
            new_offset = line_end

    return tuple(records), tuple(incidents), new_offset, parsed_count, truncated


def iter_records(poll: HermesLogPoll) -> Iterable[LogRecord]:
    """Tiny convenience for the common ``for r in poll.records`` path.

    Exists so callers don't have to know whether records are a tuple
    vs. list vs. generator — keeps signature flexibility for future
    streaming variants.
    """
    return poll.records


__all__ = [
    "HermesLogCursor",
    "HermesLogPoll",
    "iter_records",
    "poll_hermes_logs",
]
