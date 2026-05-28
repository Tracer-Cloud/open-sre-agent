"""Agent-callable Hermes log inspection tool.

Exposes :func:`get_hermes_logs` to the investigation planner so the
agent can read its own ``~/.hermes/logs/errors.log`` (or any file it's
configured to watch) without re-implementing the polling logic. The
heavy lifting lives in :mod:`app.hermes.poller`; this module is the
thin presentation layer:

* declares the tool metadata, input schema, and use-cases
* serialises :class:`HermesLogPoll` into JSON-safe primitives
* opportunistically computes incident summaries for the planner

Two modes:

``op="scan"``
    One-shot read of the most recent ``N`` log lines. Useful for
    "what's been going wrong?" questions where the agent wants a
    snapshot. Returns lines, parsed records, and any incidents the
    classifier would emit on this window.

``op="tail"``
    Incremental, cursor-driven poll. The caller passes a ``cursor``
    token returned by a previous call; the tool yields only lines
    that appeared since. Rotation- and truncation-safe. This is the
    efficient mode for repeated polling — bandwidth is O(new lines)
    not O(file size).

    **Known limitation — classifier state is not persisted between
    calls.** Each invocation constructs a fresh
    :class:`~app.hermes.classifier.IncidentClassifier`, so burst-window
    counts and traceback-continuation state reset on every tool call.
    Multi-call burst detection will therefore under-count incidents
    whose constituent records span two separate ``tail`` invocations.
    The production :class:`~app.hermes.agent.HermesAgent` avoids this by
    keeping a single long-lived classifier; agents that need accurate
    cross-poll burst detection should use the watch command instead of
    repeated ``tail`` calls.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, LogLevel, LogRecord
from app.hermes.poller import HermesLogCursor, HermesLogPoll, poll_hermes_logs
from app.tools.tool_decorator import tool

# Default location of Hermes' own error log. The agent tool resolves
# this lazily so a non-default ``$HERMES_HOME`` is respected without
# import-time side effects.
_ENV_LOG_PATH: str = "HERMES_LOG_PATH"
_DEFAULT_LOG_RELATIVE: tuple[str, ...] = (".hermes", "logs", "errors.log")

# Cap how many records the tool will serialise into a single
# response. The poller has its own byte budget; this is the
# token-budget guard for the LLM's context.
_MAX_RECORDS_PER_CALL: int = 200
_MAX_INCIDENTS_PER_CALL: int = 50


def _default_log_path() -> Path:
    """Resolve the Hermes log file path with environment override."""
    override = os.environ.get(_ENV_LOG_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home().joinpath(*_DEFAULT_LOG_RELATIVE)


def _allowed_log_dirs() -> tuple[Path, ...]:
    """Directories the tool is permitted to read from.

    By default this is ``~/.hermes`` — the directory tree that the
    Hermes agent writes to. When ``HERMES_LOG_PATH`` is set to a path
    outside that tree the env-var parent is added automatically, so
    operators with non-standard log locations don't need extra config.
    """
    dirs: list[Path] = [Path.home() / ".hermes"]
    override = os.environ.get(_ENV_LOG_PATH, "").strip()
    if override:
        dirs.append(Path(override).expanduser().resolve(strict=False).parent)
    return tuple(dirs)


def _validate_log_path(path: Path) -> None:
    """Raise ``ValueError`` if *path* is outside the allowed log directories.

    The ``log_path`` parameter is LLM-supplied and therefore untrusted.
    Without this guard a crafted call could read arbitrary files (e.g.
    ``/etc/shadow``) by passing them as ``log_path``. We resolve to an
    absolute path with no symlink traversal (``strict=False`` so
    missing files are still validated) before comparing.
    """
    resolved = path.expanduser().resolve(strict=False)
    for allowed in _allowed_log_dirs():
        try:
            resolved.relative_to(allowed.resolve(strict=False))
            return
        except ValueError:
            continue
    raise ValueError(
        f"log_path {str(path)!r} is outside the permitted log directories; "
        "set HERMES_LOG_PATH to allow a custom location"
    )


def _serialise_record(record: LogRecord) -> dict[str, Any]:
    return {
        "timestamp": record.timestamp.isoformat() if not record.is_continuation else None,
        "level": record.level.value,
        "logger": record.logger,
        "message": record.message,
        "raw": record.raw,
        "run_id": record.run_id,
        "is_continuation": record.is_continuation,
    }


def _serialise_incident(incident: HermesIncident) -> dict[str, Any]:
    return {
        "rule": incident.rule,
        "severity": incident.severity.value,
        "title": incident.title,
        "logger": incident.logger,
        "fingerprint": incident.fingerprint,
        "detected_at": incident.detected_at.isoformat(),
        "record_count": len(incident.records),
        "run_id": incident.run_id,
    }


def _parse_level_filter(levels: list[str] | None) -> frozenset[LogLevel] | None:
    if not levels:
        return None
    parsed: set[LogLevel] = set()
    for raw in levels:
        try:
            parsed.add(LogLevel(raw.strip().upper()))
        except ValueError as exc:
            raise ValueError(
                f"unknown log level {raw!r}; expected one of "
                + ", ".join(level.value for level in LogLevel)
            ) from exc
    return frozenset(parsed)


def _serialise_poll(
    poll: HermesLogPoll,
    *,
    max_records: int,
    max_incidents: int,
    keep_most_recent: bool = False,
) -> dict[str, Any]:
    if keep_most_recent and len(poll.records) > max_records:
        # 'scan' wants the most recent records — drop from the front,
        # not the tail, so the response shows what just happened.
        records = poll.records[-max_records:]
    else:
        records = poll.records[:max_records]
    truncated_in_response = len(poll.records) - len(records)
    incidents = poll.incidents[:max_incidents]

    # In scan mode the seek-back heuristic may overshoot and yield more
    # records than ``tail_lines``; the excess was already dropped above
    # via ``keep_most_recent``. Setting ``has_more`` based solely on
    # that overshoot would mislead the caller into an unnecessary
    # follow-up tail call. Suppress it when we are in scan mode (i.e.
    # ``keep_most_recent``) and the only driver is
    # ``truncated_response_records`` from the overshoot.
    if keep_most_recent:
        has_more = poll.truncated_lines > 0 or poll.rotation_detected
    else:
        has_more = poll.truncated_lines > 0 or truncated_in_response > 0 or poll.rotation_detected

    return {
        "cursor": poll.cursor.to_token(),
        "rotation_detected": poll.rotation_detected,
        # ``truncated_lines`` is what the poller dropped under its
        # own cap; ``truncated_response_records`` is what we dropped
        # from THIS response under our token-budget cap. Surface both
        # so the agent knows it should re-poll with the cursor.
        "truncated_lines": poll.truncated_lines,
        "truncated_response_records": truncated_in_response,
        "parsed_line_count": poll.parsed_line_count,
        "records": [_serialise_record(r) for r in records],
        "incidents": [_serialise_incident(i) for i in incidents],
        "has_more": has_more,
    }


@tool(
    name="get_hermes_logs",
    display_name="Hermes log poll",
    source="hermes",
    description=(
        "Read Hermes Agent's own ~/.hermes/logs/errors.log (or another "
        "Hermes log file) incrementally. Use op='scan' for a one-shot "
        "read of the last N records or op='tail' for cursor-driven "
        "incremental polling that only returns lines new since the "
        "previous call. Records are parsed and any incidents the "
        "classifier would emit on this window are included."
    ),
    use_cases=[
        "Investigating why the agent itself is failing (gateway crashes, "
        + "auth bypass, polling conflicts)",
        "Following a Hermes log live during an active incident without "
        + "re-reading the entire file on every call",
        "Surfacing structured incidents (error_severity, traceback, "
        + "warning_burst) from a slice of recent log activity",
    ],
    tags=("safe", "fast", "no-credentials"),
    cost_tier="cheap",
    input_schema={
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["scan", "tail"],
                "default": "scan",
                "description": (
                    "'scan' for a one-shot read; 'tail' for cursor-driven incremental polling."
                ),
            },
            "log_path": {
                "type": "string",
                "description": (
                    "Path to the Hermes log file. Defaults to "
                    "$HERMES_LOG_PATH or ~/.hermes/logs/errors.log."
                ),
            },
            "cursor": {
                "type": "string",
                "description": (
                    "Opaque resume token returned by a previous call. "
                    "Required for op='tail' on the second+ call. "
                    "Ignored for op='scan'."
                ),
            },
            "tail_lines": {
                "type": "integer",
                "default": 200,
                "minimum": 1,
                "maximum": _MAX_RECORDS_PER_CALL,
                "description": (
                    "For op='scan': how many recent records to return. Ignored for op='tail'."
                ),
            },
            "max_records": {
                "type": "integer",
                "default": _MAX_RECORDS_PER_CALL,
                "minimum": 1,
                "maximum": _MAX_RECORDS_PER_CALL,
                "description": (
                    "Upper bound on records included in the response. "
                    "Hits truncated_response_records when exceeded."
                ),
            },
            "levels": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [level.value for level in LogLevel],
                },
                "description": (
                    "Only return records at these levels. The "
                    "classifier still observes filtered records so "
                    "traceback continuations and warning-burst windows "
                    "remain accurate."
                ),
            },
        },
        "required": [],
    },
)
def get_hermes_logs(
    op: str = "scan",
    log_path: str | None = None,
    cursor: str | None = None,
    tail_lines: int = 200,
    max_records: int = _MAX_RECORDS_PER_CALL,
    levels: list[str] | None = None,
) -> dict[str, Any]:
    """Read Hermes log activity. See module docstring for op semantics."""
    if op not in {"scan", "tail"}:
        return {
            "error": f"unknown op {op!r}; expected 'scan' or 'tail'",
            "records": [],
            "incidents": [],
        }

    try:
        level_filter = _parse_level_filter(levels)
    except ValueError as exc:
        return {"error": str(exc), "records": [], "incidents": []}

    resolved_path = Path(log_path).expanduser() if log_path else _default_log_path()

    try:
        _validate_log_path(resolved_path)
    except ValueError as exc:
        return {"error": str(exc), "records": [], "incidents": []}

    resolved_cursor: HermesLogCursor | None
    if op == "scan":
        # 'scan' always rewinds to the end of the file minus tail_lines
        # worth of bytes (estimated at 480 bytes/line — aligns with the
        # seek-back heuristic in ``_seek_back_n_lines``, below).
        # The poller then reads forward;
        # we cap to tail_lines in the response.
        bounded_tail = max(1, min(tail_lines, _MAX_RECORDS_PER_CALL))
        resolved_cursor = _seek_back_n_lines(resolved_path, bounded_tail)
        bounded_max = min(max_records, bounded_tail)
    elif cursor:
        try:
            resolved_cursor = HermesLogCursor.from_token(cursor)
            # Tokens are LLM-round-tripped; reject crafted paths that
            # do not match the log file this invocation is configured to read.
            resolved_cursor.validate_expected_log_path(resolved_path)
        except ValueError as exc:
            return {"error": str(exc), "records": [], "incidents": []}
        bounded_max = min(max_records, _MAX_RECORDS_PER_CALL)
    else:
        # op='tail' without a cursor: anchor at end-of-file so the
        # very first tail call doesn't replay the entire backlog.
        resolved_cursor = HermesLogCursor.at_end(resolved_path)
        bounded_max = min(max_records, _MAX_RECORDS_PER_CALL)

    classifier = IncidentClassifier()
    try:
        poll = poll_hermes_logs(
            resolved_path,
            resolved_cursor,
            max_lines=_MAX_RECORDS_PER_CALL,
            classifier=classifier,
            level_filter=level_filter,
        )
    except PermissionError as exc:
        return {
            "error": f"permission denied reading {resolved_path}: {exc}",
            "records": [],
            "incidents": [],
        }

    flushed = tuple(classifier.flush())
    if flushed:
        poll = replace(poll, incidents=poll.incidents + flushed)

    return _serialise_poll(
        poll,
        max_records=bounded_max,
        max_incidents=_MAX_INCIDENTS_PER_CALL,
        keep_most_recent=(op == "scan"),
    )


def _seek_back_n_lines(path: Path, n: int) -> HermesLogCursor:
    """Best-effort cursor that points roughly ``n`` lines before EOF.

    We can't cheaply count lines from the end of a file without
    reading it, so we estimate a generous 480 bytes per line (Hermes
    records often include long tracebacks). For small files the
    estimate may exceed the file size — in that case we just read
    from offset 0 so the response naturally contains the last N
    records of the available log.
    """
    bytes_per_line_estimate = 480
    try:
        stat = path.stat()
    except (FileNotFoundError, PermissionError):
        return HermesLogCursor.at_start(path)
    needed_bytes = n * bytes_per_line_estimate
    offset = max(0, stat.st_size - needed_bytes)
    # If we'd land mid-line, snap forward to the next newline so the
    # parser doesn't see a truncated first record. Skipped when we're
    # already at offset 0 (start of file is always a clean line edge).
    if offset > 0:
        offset = _next_line_offset(path, offset)
    return HermesLogCursor(path=str(path), device=stat.st_dev, inode=stat.st_ino, offset=offset)


def _next_line_offset(path: Path, offset: int) -> int:
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            handle.readline()  # advance to start of next line
            return handle.tell()
    except OSError:
        return offset


__all__ = ["get_hermes_logs"]
