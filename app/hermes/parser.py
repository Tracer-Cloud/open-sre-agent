"""Parser for Hermes ``errors.log`` lines into :class:`LogRecord`.

The Hermes log format is the standard Python ``logging`` default with a
millisecond timestamp, e.g.::

    2026-05-12 00:12:17,243 ERROR [run_id] logger.name: message
    2026-05-11 23:31:15,063 WARNING gateway.platforms.telegram: message

The optional ``[run_id]`` segment is emitted by ``run_agent`` style loggers
and threaded through downstream records so the classifier can group an
incident's contributing lines by run. Continuation lines (traceback frames,
multi-line ``repr`` output) do not match the timestamp prefix and are
represented as records with ``is_continuation=True`` and an empty logger.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.hermes.incident import LogLevel, LogRecord

# ``logging``'s default formatter writes ``%Y-%m-%d %H:%M:%S,%f`` truncated to
# milliseconds. Match it strictly so we don't misclassify a rogue ISO-8601
# message body as a fresh record.
_HEADER_RE = re.compile(
    r"""
    ^
    (?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})
    \s+
    (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)
    \s+
    (?:\[(?P<run_id>[^\]]+)\]\s+)?
    (?P<logger>[^\s:][^:]*?)
    :\s
    (?P<message>.*)
    $
    """,
    re.VERBOSE,
)

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S,%f"


def parse_log_line(line: str, *, prev_level: LogLevel | None = None) -> LogRecord | None:
    """Parse a single log line into a :class:`LogRecord`.

    Returns ``None`` for empty lines (after stripping the trailing newline)
    so the caller can skip blank padding without allocating a record.

    Continuation lines (no recognized header) inherit ``prev_level`` so a
    multi-line traceback keeps its severity for downstream filters; if
    ``prev_level`` is ``None`` the continuation defaults to ``ERROR`` which
    is the only level Hermes actually writes multi-line payloads at today.
    """
    stripped = line.rstrip("\r\n")
    if not stripped:
        return None

    match = _HEADER_RE.match(stripped)
    if match is None:
        return _continuation_record(stripped, prev_level)

    try:
        timestamp = datetime.strptime(match["timestamp"], _TIMESTAMP_FMT)
    except ValueError:
        return _continuation_record(stripped, prev_level)

    level_raw = match["level"]
    try:
        level = LogLevel(level_raw)
    except ValueError:
        # Unknown levels are treated as continuations rather than dropped so
        # operators don't lose visibility on novel logging configurations.
        return _continuation_record(stripped, prev_level)

    return LogRecord(
        timestamp=timestamp,
        level=level,
        logger=match["logger"].strip(),
        message=match["message"],
        raw=stripped,
        run_id=match["run_id"],
    )


def _continuation_record(stripped: str, prev_level: LogLevel | None) -> LogRecord:
    return LogRecord(
        timestamp=datetime.min,
        level=prev_level if prev_level is not None else LogLevel.ERROR,
        logger="",
        message=stripped,
        raw=stripped,
        is_continuation=True,
    )


__all__ = ["parse_log_line"]
