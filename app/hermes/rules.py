"""Pluggable pattern-rule registry for the Hermes incident classifier.

The built-in :class:`IncidentClassifier` ships with three structural rules
(``error_severity``, ``traceback``, ``warning_burst``). Those cover *log
shape* — a record's level or a sequence of records — but they don't know
anything about the *content* that has actually broken Hermes in
production: OOM kills, crash loops, context-window overruns, auth
bypasses, rate limits, WAL growth, etc.

Pattern rules close that gap. A :class:`PatternRule` matches a
:class:`LogRecord`'s message against one or more regex patterns and
emits a :class:`HermesIncident` with a stable ``rule`` name. The
classifier accepts an arbitrary list of pattern rules at construction
time, so operators can ship their own without modifying core logic.

The default registry — :func:`default_pattern_rules` — encodes the
operational failure modes observed in the synthetic test corpus
(``tests/synthetic/hermes/scenarios/``), all of which trace back to
real hermes-agent GitHub issues:

* ``oom_killed``               — Linux OOM-killer / Python ``MemoryError``
* ``crash_loop``               — repeated agent restart in a window
* ``context_window_exceeded``  — model token-limit overruns
* ``auth_failure``             — token / auth bypass anomalies
* ``rate_limit``               — provider 429 / quota exceeded
* ``database_wal_growth``      — sqlite / postgres WAL pressure
* ``deadlock``                 — explicit deadlock detection
* ``disk_full``                — ENOSPC / no space left on device
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord


@dataclass(frozen=True, slots=True)
class PatternRule:
    """Rule that emits one incident per matching log record.

    Patterns are matched case-insensitively against the record's
    message. Any pattern matching causes the rule to fire.
    """

    name: str
    severity: IncidentSeverity
    title_template: str
    patterns: tuple[re.Pattern[str], ...]
    min_level: LogLevel = LogLevel.WARNING

    def evaluate(self, record: LogRecord) -> HermesIncident | None:
        if record.is_continuation:
            return None
        if record.level.severity_rank < self.min_level.severity_rank:
            return None
        for pattern in self.patterns:
            if pattern.search(record.message) or pattern.search(record.raw):
                title = self.title_template.format(
                    logger=record.logger or "unknown",
                    level=record.level.value,
                )
                return HermesIncident(
                    rule=self.name,
                    severity=self.severity,
                    title=title,
                    detected_at=record.timestamp,
                    logger=record.logger,
                    fingerprint=_fingerprint(self.name, record.logger, pattern.pattern),
                    records=(record,),
                    run_id=record.run_id,
                )
        return None


@dataclass
class RepeatRule:
    """Rule that fires when the same pattern matches N times in a window.

    Used for ``crash_loop`` and any other failure mode that's only
    actionable when it repeats. Each rule keeps its own bounded deque
    keyed by logger; older matches age out automatically.
    """

    name: str
    severity: IncidentSeverity
    title_template: str
    patterns: tuple[re.Pattern[str], ...]
    threshold: int
    window: timedelta
    min_level: LogLevel = LogLevel.WARNING
    _hits: dict[str, deque[LogRecord]] = field(default_factory=dict)

    def evaluate(self, record: LogRecord) -> HermesIncident | None:
        if record.is_continuation:
            return None
        if record.level.severity_rank < self.min_level.severity_rank:
            return None
        if not any(p.search(record.message) or p.search(record.raw) for p in self.patterns):
            return None
        key = record.logger or "_unknown"
        bucket = self._hits.setdefault(key, deque())
        bucket.append(record)
        cutoff = record.timestamp - self.window
        while bucket and bucket[0].timestamp < cutoff:
            bucket.popleft()
        if len(bucket) < self.threshold:
            return None
        contributing = tuple(bucket)
        bucket.clear()
        title = self.title_template.format(
            logger=record.logger or "unknown",
            count=len(contributing),
            seconds=int(self.window.total_seconds()),
        )
        return HermesIncident(
            rule=self.name,
            severity=self.severity,
            title=title,
            detected_at=record.timestamp,
            logger=record.logger,
            fingerprint=_fingerprint(self.name, record.logger, ""),
            records=contributing,
            run_id=record.run_id,
        )


def default_pattern_rules() -> list[PatternRule | RepeatRule]:
    """Return the default operational pattern-rule set.

    Patterns are curated from real hermes-agent issue reports (see
    ``tests/synthetic/hermes/scenarios/``); each is intentionally
    permissive about surrounding text so logger format changes don't
    silently break detection.
    """
    return [
        PatternRule(
            name="oom_killed",
            severity=IncidentSeverity.CRITICAL,
            title_template="Out-of-memory event in {logger}",
            patterns=(
                re.compile(r"\bMemoryError\b", re.IGNORECASE),
                re.compile(r"out of memory", re.IGNORECASE),
                re.compile(r"\boom[- _]killer", re.IGNORECASE),
                re.compile(r"killed process \d+", re.IGNORECASE),
                re.compile(r"cgroup .* out of memory", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="context_window_exceeded",
            severity=IncidentSeverity.HIGH,
            title_template="Context-window overrun in {logger}",
            patterns=(
                re.compile(r"context[_ -]?length[_ -]?exceeded", re.IGNORECASE),
                re.compile(r"context window", re.IGNORECASE),
                re.compile(r"maximum context length", re.IGNORECASE),
                re.compile(r"\d{4,}\s+tokens?.*(?:exceed|over|limit|max)", re.IGNORECASE),
                re.compile(r"prompt is too long", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="auth_failure",
            severity=IncidentSeverity.HIGH,
            title_template="Authentication failure in {logger}",
            patterns=(
                re.compile(r"\b401\b.*unauthori[sz]ed", re.IGNORECASE),
                re.compile(r"\b403\b.*forbidden", re.IGNORECASE),
                re.compile(r"invalid (?:api[- _]?key|token|credentials)", re.IGNORECASE),
                re.compile(r"auth(?:entication)?\s+(?:failed|denied|bypass)", re.IGNORECASE),
                re.compile(r"signature (?:mismatch|invalid)", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="rate_limit",
            severity=IncidentSeverity.MEDIUM,
            title_template="Rate-limit / quota hit in {logger}",
            patterns=(
                re.compile(r"\b429\b", re.IGNORECASE),
                re.compile(r"rate[- ]?limit(?:ed|ing|exceeded)?", re.IGNORECASE),
                re.compile(r"quota (?:exceeded|exhausted)", re.IGNORECASE),
                re.compile(r"too many requests", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="database_wal_growth",
            severity=IncidentSeverity.HIGH,
            title_template="Database WAL pressure in {logger}",
            patterns=(
                re.compile(r"\bWAL\b.*(?:grow|size|bytes|backlog)", re.IGNORECASE),
                re.compile(r"write[- ]?ahead[- ]?log", re.IGNORECASE),
                re.compile(r"checkpoint .* (?:lagging|behind|failed)", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="deadlock",
            severity=IncidentSeverity.HIGH,
            title_template="Deadlock detected in {logger}",
            patterns=(
                re.compile(r"deadlock detected", re.IGNORECASE),
                re.compile(r"\bdeadlock\b.*(?:victim|abort|rollback)", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        PatternRule(
            name="disk_full",
            severity=IncidentSeverity.CRITICAL,
            title_template="Disk full in {logger}",
            patterns=(
                re.compile(r"no space left on device", re.IGNORECASE),
                re.compile(r"\bENOSPC\b"),
                re.compile(r"disk (?:is )?full", re.IGNORECASE),
            ),
            min_level=LogLevel.WARNING,
        ),
        RepeatRule(
            name="crash_loop",
            severity=IncidentSeverity.CRITICAL,
            title_template="Crash loop in {logger}: {count} restarts in {seconds}s",
            patterns=(
                re.compile(
                    r"(?:agent|process|service|runtime)\s+(?:re)?start(?:ing|ed)?",
                    re.IGNORECASE,
                ),
                re.compile(r"unexpected (?:exit|shutdown|termination)", re.IGNORECASE),
                re.compile(r"exited with (?:status|code)\s*\d+", re.IGNORECASE),
            ),
            threshold=3,
            window=timedelta(seconds=120),
            min_level=LogLevel.WARNING,
        ),
    ]


_FINGERPRINT_SEPARATOR: Final[str] = "|"


def _fingerprint(rule: str, logger_name: str | None, extra: str) -> str:
    import hashlib

    digest = hashlib.sha1(
        f"{rule}{_FINGERPRINT_SEPARATOR}{logger_name or ''}{_FINGERPRINT_SEPARATOR}{extra}".encode(),
        usedforsecurity=False,
    )
    return digest.hexdigest()[:16]


def evaluate_all(
    rules: Sequence[PatternRule | RepeatRule],
    records: Iterable[LogRecord],
) -> list[HermesIncident]:
    """Run a fresh pass of *rules* across *records* in order."""
    incidents: list[HermesIncident] = []
    for record in records:
        for rule in rules:
            incident = rule.evaluate(record)
            if incident is not None:
                incidents.append(incident)
    return incidents


__all__ = [
    "PatternRule",
    "RepeatRule",
    "default_pattern_rules",
    "evaluate_all",
]
