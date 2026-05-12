"""Rule-based incident classifier for parsed Hermes log records.

The classifier consumes :class:`LogRecord` objects in chronological order
and emits :class:`HermesIncident` events. It is intentionally rule-based
(no ML, no LLM) so detection latency is bounded and behavior is auditable.

Rules (each maps to a stable ``rule`` string used for deduplication):

* ``error_severity``  – any ``ERROR`` or ``CRITICAL`` record. Severity is
  ``HIGH`` for ``ERROR`` and ``CRITICAL`` for ``CRITICAL``.
* ``traceback``       – a ``Traceback (most recent call last):`` line plus
  its continuation frames. Severity ``CRITICAL``. Continuation lines are
  attached to the parent until a non-continuation record arrives.
* ``warning_burst``   – ``warning_burst_threshold`` ``WARNING`` records
  from the same logger within ``warning_burst_window_s``. Severity
  ``MEDIUM``. The burst is debounced by logger so a single noisy
  subsystem fires once per burst rather than once per warning.

The classifier is stateful but thread-safe: one ``threading.Lock`` guards
all bucket mutations. :meth:`observe` is intended to be called from a
single producer thread (the agent's tailer pump); the lock is defensive
and primarily exists so external callers can flush from another thread.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from app.hermes.rules import PatternRule, RepeatRule, default_pattern_rules

DEFAULT_WARNING_BURST_THRESHOLD: Final[int] = 5
DEFAULT_WARNING_BURST_WINDOW_S: Final[float] = 60.0
DEFAULT_TRACEBACK_FOLLOWUP_S: Final[float] = 5.0

_TRACEBACK_HEADER: Final[str] = "Traceback (most recent call last)"
_IPV4_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HEX_RE: Final[re.Pattern[str]] = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NUM_RE: Final[re.Pattern[str]] = re.compile(r"\b\d+\b")
_WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass
class _OpenTraceback:
    parent: LogRecord
    frames: list[LogRecord]
    deadline: datetime


class IncidentClassifier:
    """Stateful classifier that turns log records into incidents."""

    __slots__ = (
        "_warning_burst_threshold",
        "_warning_burst_window",
        "_traceback_followup",
        "_warning_buckets",
        "_open_tracebacks",
        "_lock",
        "_pattern_rules",
    )

    def __init__(
        self,
        *,
        warning_burst_threshold: int = DEFAULT_WARNING_BURST_THRESHOLD,
        warning_burst_window_s: float = DEFAULT_WARNING_BURST_WINDOW_S,
        traceback_followup_s: float = DEFAULT_TRACEBACK_FOLLOWUP_S,
        pattern_rules: list[PatternRule | RepeatRule] | None = None,
        use_default_pattern_rules: bool = True,
    ) -> None:
        if warning_burst_threshold < 2:
            raise ValueError("warning_burst_threshold must be >= 2")
        if warning_burst_window_s <= 0:
            raise ValueError("warning_burst_window_s must be > 0")
        if traceback_followup_s < 0:
            raise ValueError("traceback_followup_s must be >= 0")

        self._warning_burst_threshold = warning_burst_threshold
        self._warning_burst_window = timedelta(seconds=warning_burst_window_s)
        self._traceback_followup = timedelta(seconds=traceback_followup_s)
        self._warning_buckets: dict[str, deque[LogRecord]] = {}
        self._open_tracebacks: dict[str, _OpenTraceback] = {}
        self._lock = threading.Lock()
        rules: list[PatternRule | RepeatRule] = []
        if use_default_pattern_rules:
            rules.extend(default_pattern_rules())
        if pattern_rules:
            rules.extend(_clone_rule(rule) for rule in pattern_rules)
        self._pattern_rules = rules

    def observe(self, record: LogRecord) -> list[HermesIncident]:
        """Feed a single record; return any incidents triggered by it.

        The order of incidents matches the order rules are evaluated
        (traceback close > severity > warning burst).
        """
        incidents: list[HermesIncident] = []

        with self._lock:
            incidents.extend(self._collect_finalized_tracebacks(record.timestamp))

            if record.is_continuation:
                self._extend_open_tracebacks(record)
                return incidents

            traceback_incident = self._maybe_open_or_finalize_traceback(record)
            if traceback_incident is not None:
                incidents.append(traceback_incident)

            severity_incident = self._maybe_emit_severity(record)
            if severity_incident is not None:
                incidents.append(severity_incident)

            burst_incident = self._maybe_emit_warning_burst(record)
            if burst_incident is not None:
                incidents.append(burst_incident)

            for rule in self._pattern_rules:
                pattern_incident = rule.evaluate(record)
                if pattern_incident is not None:
                    incidents.append(pattern_incident)

        return incidents

    def flush(self, *, now: datetime | None = None) -> list[HermesIncident]:
        """Force-emit any buffered tracebacks.

        Used at shutdown so a traceback whose continuation frames never
        receive a follow-up record still surfaces as an incident.
        """
        cutoff = now if now is not None else datetime.max
        with self._lock:
            return self._collect_finalized_tracebacks(cutoff, force=True)

    def _maybe_emit_severity(self, record: LogRecord) -> HermesIncident | None:
        if record.level.severity_rank < LogLevel.ERROR.severity_rank:
            return None
        # Traceback headers are handled exclusively by
        # _maybe_open_or_finalize_traceback which will emit a ``traceback``
        # incident (CRITICAL, with frames) once the block is complete.
        # Emitting ``error_severity`` for the same record would create two
        # separate incidents for every Python exception — different
        # fingerprints, different dedup buckets — resulting in duplicate
        # Telegram notifications and two concurrent RCA investigation calls.
        if _looks_like_traceback_header(record):
            return None
        severity = (
            IncidentSeverity.CRITICAL
            if record.level is LogLevel.CRITICAL
            else IncidentSeverity.HIGH
        )
        return HermesIncident(
            rule="error_severity",
            severity=severity,
            title=f"{record.level.value} from {record.logger or 'unknown'}",
            detected_at=record.timestamp,
            logger=record.logger,
            fingerprint=_fingerprint(
                "error_severity",
                record.logger,
                _message_signature(record.message),
            ),
            records=(record,),
            run_id=record.run_id,
        )

    def _maybe_emit_warning_burst(self, record: LogRecord) -> HermesIncident | None:
        if record.level is not LogLevel.WARNING or not record.logger:
            return None
        bucket = self._warning_buckets.setdefault(record.logger, deque())
        bucket.append(record)
        cutoff = record.timestamp - self._warning_burst_window
        while bucket and bucket[0].timestamp < cutoff:
            bucket.popleft()
        if len(bucket) < self._warning_burst_threshold:
            return None
        # Drain the bucket on emit so the next burst requires a fresh
        # threshold's worth of warnings rather than re-firing every line.
        contributing = tuple(bucket)
        bucket.clear()
        return HermesIncident(
            rule="warning_burst",
            severity=IncidentSeverity.MEDIUM,
            title=(
                f"{len(contributing)} warnings from {record.logger} "
                f"in {self._warning_burst_window.total_seconds():.0f}s"
            ),
            detected_at=record.timestamp,
            logger=record.logger,
            fingerprint=_fingerprint("warning_burst", record.logger, ""),
            records=contributing,
            run_id=record.run_id,
        )

    def _maybe_open_or_finalize_traceback(self, record: LogRecord) -> HermesIncident | None:
        # Close the open traceback for this logger only when a new
        # non-continuation record arrives from the *same* logger; that's
        # the python-logging signal that the traceback's frames are done.
        if not record.logger:
            return None

        finalized: HermesIncident | None = None
        existing = self._open_tracebacks.pop(record.logger, None)
        if existing is not None:
            finalized = _build_traceback_incident(existing)

        if _looks_like_traceback_header(record):
            self._open_tracebacks[record.logger] = _OpenTraceback(
                parent=record,
                frames=[],
                deadline=record.timestamp + self._traceback_followup,
            )

        return finalized

    def _extend_open_tracebacks(self, record: LogRecord) -> None:
        # Continuations don't carry a logger, so attach to every open
        # traceback. In practice Hermes writes one traceback at a time,
        # so this is at most one entry; the loop is defensive.
        for state in self._open_tracebacks.values():
            state.frames.append(record)

    def _collect_finalized_tracebacks(
        self,
        now: datetime,
        *,
        force: bool = False,
    ) -> list[HermesIncident]:
        if not self._open_tracebacks:
            return []
        emitted: list[HermesIncident] = []
        for logger_name in list(self._open_tracebacks):
            state = self._open_tracebacks[logger_name]
            if not force and now < state.deadline:
                continue
            del self._open_tracebacks[logger_name]
            emitted.append(_build_traceback_incident(state))
        return emitted


def classify_all(records: Iterable[LogRecord]) -> list[HermesIncident]:
    """Convenience: run a fresh classifier over a finite record stream."""
    classifier = IncidentClassifier()
    incidents: list[HermesIncident] = []
    for record in records:
        incidents.extend(classifier.observe(record))
    incidents.extend(classifier.flush())
    return incidents


def _looks_like_traceback_header(record: LogRecord) -> bool:
    return _TRACEBACK_HEADER in record.message


def _build_traceback_incident(state: _OpenTraceback) -> HermesIncident:
    records = (state.parent, *state.frames)
    return HermesIncident(
        rule="traceback",
        severity=IncidentSeverity.CRITICAL,
        title=f"Traceback in {state.parent.logger}",
        detected_at=state.parent.timestamp,
        logger=state.parent.logger,
        fingerprint=_fingerprint("traceback", state.parent.logger, state.parent.message),
        records=records,
        run_id=state.parent.run_id,
    )


def _fingerprint(rule: str, logger_name: str, message: str) -> str:
    digest = hashlib.sha1(
        f"{rule}|{logger_name}|{message}".encode(),
        usedforsecurity=False,
    )
    return digest.hexdigest()[:16]


def _message_signature(message: str) -> str:
    """Normalize volatile values so dedup keys stay stable across retries."""
    normalized = message.lower()
    normalized = _IPV4_RE.sub("<ip>", normalized)
    normalized = _HEX_RE.sub("<hex>", normalized)
    normalized = _NUM_RE.sub("<num>", normalized)
    normalized = _WS_RE.sub(" ", normalized).strip()
    return normalized[:120]


def _clone_rule(rule: PatternRule | RepeatRule) -> PatternRule | RepeatRule:
    """Return an equivalent rule instance with isolated mutable state.

    PatternRule is immutable so reuse is safe. RepeatRule carries mutable
    per-logger hit buckets; cloning prevents accidental cross-classifier
    state sharing when callers pass the same rule instance to multiple
    IncidentClassifier objects.
    """
    if isinstance(rule, PatternRule):
        return rule
    return RepeatRule(
        name=rule.name,
        severity=rule.severity,
        title_template=rule.title_template,
        patterns=rule.patterns,
        threshold=rule.threshold,
        window=rule.window,
    )


__all__ = [
    "DEFAULT_TRACEBACK_FOLLOWUP_S",
    "DEFAULT_WARNING_BURST_THRESHOLD",
    "DEFAULT_WARNING_BURST_WINDOW_S",
    "IncidentClassifier",
    "classify_all",
]
