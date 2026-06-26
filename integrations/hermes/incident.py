"""Typed records for Hermes log lines and detected incidents.

These types are deliberately lightweight (frozen dataclasses) so they can be
passed across thread boundaries from the tailer/parser into the classifier
and out to subscribers without locking concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final


class LogLevel(StrEnum):
    """Subset of Python logging levels we track from Hermes logs."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def severity_rank(self) -> int:
        # Used for severity comparisons in the classifier; higher = worse.
        return _LEVEL_RANK[self]


_LEVEL_RANK: Final[dict[LogLevel, int]] = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}


class IncidentSeverity(StrEnum):
    """Incident severity emitted by the classifier."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class LogRecord:
    """A single parsed Hermes log line.

    ``raw`` retains the original line (without its trailing newline) so
    downstream consumers can render the unmodified source for debugging.
    Continuation lines (e.g. traceback frames) appear as records with the
    same level as their parent and an empty ``logger``.
    """

    timestamp: datetime
    level: LogLevel
    logger: str
    message: str
    raw: str
    run_id: str | None = None
    is_continuation: bool = False


@dataclass(frozen=True, slots=True)
class HermesIncident:
    """An incident identified from one or more Hermes log records.

    ``rule`` is the classifier rule that produced the incident (e.g.
    ``"error_severity"``, ``"warning_burst"``, ``"traceback"``); it is
    stable across runs and intended for both routing and metrics.

    ``fingerprint`` is a deterministic identifier derived from the rule
    and the contributing logger/message so downstream alerting can
    deduplicate without re-parsing the records.
    """

    rule: str
    severity: IncidentSeverity
    title: str
    detected_at: datetime
    logger: str
    fingerprint: str
    records: tuple[LogRecord, ...] = field(default_factory=tuple)
    run_id: str | None = None


__all__ = [
    "HermesIncident",
    "IncidentSeverity",
    "LogLevel",
    "LogRecord",
]
