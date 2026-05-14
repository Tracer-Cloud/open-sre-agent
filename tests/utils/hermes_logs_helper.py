"""Shared Hermes-log polling helper for the test suite.

This is the test-side counterpart to ``app.tools.HermesLogsTool``: it
wraps :func:`app.hermes.poller.poll_hermes_logs` with the small
ergonomics every Hermes test ends up wanting — a temp-file context
manager that appends lines with realistic line endings and flushing,
a cursor-aware polling loop with a deadline, and helpers that pull
records and incidents out of the underlying primitive without each
test re-rolling its own boilerplate.

Centralising this in one helper means production code and tests
exercise the **exact same** polling engine. A regression in the poller
(rotation-safe rewind, cursor token round-trip, byte budget enforcement)
fails the synthetic suite as well as the unit tests for the tool.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, LogLevel, LogRecord
from app.hermes.poller import HermesLogCursor, HermesLogPoll, poll_hermes_logs

_DEFAULT_POLL_BUDGET_S: float = 5.0
_DEFAULT_POLL_INTERVAL_S: float = 0.02


@dataclass
class HermesLogFixture:
    """Mutable wrapper around a temp log file for tests."""

    path: Path
    cursor: HermesLogCursor
    classifier: IncidentClassifier = field(default_factory=IncidentClassifier)
    accumulated_records: list[LogRecord] = field(default_factory=list)
    accumulated_incidents: list[HermesIncident] = field(default_factory=list)

    def write_line(self, line: str) -> None:
        """Append a single log line (newline added if missing)."""
        if not line.endswith("\n"):
            line = line + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def write_lines(self, lines: Iterable[str], *, stagger_s: float = 0.0) -> None:
        """Append many lines, optionally pausing between writes.

        ``stagger_s`` mimics real-time log production for tests that
        exercise the live-tail behaviour through HermesAgent's daemon
        thread; for synchronous poll-based tests leave it at 0.
        """
        for line in lines:
            self.write_line(line)
            if stagger_s:
                time.sleep(stagger_s)

    def poll_once(
        self,
        *,
        max_lines: int | None = 2000,
        level_filter: frozenset[LogLevel] | None = None,
    ) -> HermesLogPoll:
        """Drain whatever is new and advance the cursor."""
        poll = poll_hermes_logs(
            self.path,
            self.cursor,
            max_lines=max_lines,
            classifier=self.classifier,
            level_filter=level_filter,
        )
        self.cursor = poll.cursor
        self.accumulated_records.extend(poll.records)
        self.accumulated_incidents.extend(poll.incidents)
        return poll

    def poll_until(
        self,
        predicate,
        *,
        budget_s: float = _DEFAULT_POLL_BUDGET_S,
        interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> bool:
        """Poll repeatedly until ``predicate(fixture)`` becomes True.

        Returns ``True`` if the predicate was satisfied within the
        budget, ``False`` otherwise. Useful in combination with the
        live ``HermesAgent`` daemon thread: the test writes lines,
        then waits for the classifier to surface a specific incident.

        ``predicate`` is called *after each poll* with the fixture so
        it sees the accumulated state — typical use:

        ::

            fixture.write_lines(scenario.log_lines)
            assert fixture.poll_until(
                lambda f: any(i.rule == "warning_burst" for i in f.accumulated_incidents)
            )
        """
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            self.poll_once()
            if predicate(self):
                return True
            time.sleep(interval_s)
        # Final poll after the deadline for clear error messages.
        self.poll_once()
        return predicate(self)

    def rule_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for incident in self.accumulated_incidents:
            counts[incident.rule] = counts.get(incident.rule, 0) + 1
        return counts

    def incidents_by_rule(self, rule: str) -> list[HermesIncident]:
        return [i for i in self.accumulated_incidents if i.rule == rule]


@contextmanager
def hermes_log_fixture(
    tmp_path: Path, *, filename: str = "errors.log"
) -> Iterator[HermesLogFixture]:
    """Context manager yielding a :class:`HermesLogFixture` rooted at
    ``tmp_path/filename``.

    The file is created empty (so the poller's first :func:`stat`
    captures device + inode) and the cursor is anchored at
    end-of-file so writes appended inside the block are observed by
    the next :meth:`HermesLogFixture.poll_once`. Tearing down the
    context does nothing — pytest's ``tmp_path`` fixture handles
    cleanup.
    """
    log_path = tmp_path / filename
    log_path.touch()
    cursor = HermesLogCursor.at_end(log_path)
    yield HermesLogFixture(path=log_path, cursor=cursor)


__all__ = [
    "HermesLogFixture",
    "hermes_log_fixture",
]
