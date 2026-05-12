"""Tests for :mod:`app.hermes.classifier`."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.hermes.classifier import IncidentClassifier, classify_all
from app.hermes.incident import IncidentSeverity, LogLevel, LogRecord


def _record(
    *,
    level: LogLevel = LogLevel.WARNING,
    logger: str = "gateway.platforms.telegram",
    message: str = "polling conflict",
    timestamp: datetime | None = None,
    is_continuation: bool = False,
    run_id: str | None = None,
) -> LogRecord:
    return LogRecord(
        timestamp=timestamp if timestamp is not None else datetime(2026, 5, 12, 0, 0, 0),
        level=level,
        logger=logger,
        message=message,
        raw=f"{logger}: {message}",
        run_id=run_id,
        is_continuation=is_continuation,
    )


class TestErrorSeverityRule:
    def test_error_emits_high_severity(self) -> None:
        classifier = IncidentClassifier()
        record = _record(level=LogLevel.ERROR, logger="root", message="boom")

        incidents = classifier.observe(record)

        assert len(incidents) == 1
        assert incidents[0].rule == "error_severity"
        assert incidents[0].severity is IncidentSeverity.HIGH
        assert incidents[0].records == (record,)

    def test_critical_emits_critical_severity(self) -> None:
        classifier = IncidentClassifier()
        record = _record(level=LogLevel.CRITICAL, logger="root", message="oops")

        incidents = classifier.observe(record)

        assert len(incidents) == 1
        assert incidents[0].severity is IncidentSeverity.CRITICAL

    def test_info_emits_nothing(self) -> None:
        classifier = IncidentClassifier()
        assert classifier.observe(_record(level=LogLevel.INFO)) == []
        assert classifier.observe(_record(level=LogLevel.DEBUG)) == []

    def test_error_fingerprint_normalizes_volatile_tokens(self) -> None:
        classifier = IncidentClassifier()
        a = _record(
            level=LogLevel.ERROR,
            logger="db.client",
            message="failed to connect host=10.0.0.1 port=5432 code=500",
        )
        b = _record(
            level=LogLevel.ERROR,
            logger="db.client",
            message="failed to connect host=10.0.0.2 port=5432 code=501",
        )
        ia = classifier.observe(a)[0]
        ib = classifier.observe(b)[0]

        assert ia.rule == "error_severity"
        assert ib.rule == "error_severity"
        assert ia.fingerprint == ib.fingerprint


class TestWarningBurstRule:
    def test_burst_fires_at_threshold_and_clears(self) -> None:
        classifier = IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=60.0)
        base = datetime(2026, 5, 12, 0, 0, 0)

        out: list = []
        for offset in (0, 10, 20):
            out.extend(classifier.observe(_record(timestamp=base + timedelta(seconds=offset))))

        assert len(out) == 1
        burst = out[0]
        assert burst.rule == "warning_burst"
        assert burst.severity is IncidentSeverity.MEDIUM
        assert len(burst.records) == 3

        # Bucket cleared on emit; the next single warning must not re-trigger.
        further = classifier.observe(_record(timestamp=base + timedelta(seconds=30)))
        assert further == []

    def test_warnings_outside_window_do_not_accumulate(self) -> None:
        classifier = IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=10.0)
        base = datetime(2026, 5, 12, 0, 0, 0)

        for offset in (0, 30, 60):
            assert classifier.observe(_record(timestamp=base + timedelta(seconds=offset))) == []

    def test_burst_segregated_per_logger(self) -> None:
        classifier = IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=60.0)
        base = datetime(2026, 5, 12, 0, 0, 0)

        out: list = []
        for offset, logger in [
            (0, "telegram"),
            (1, "datadog"),
            (2, "telegram"),
            (3, "datadog"),
            (4, "telegram"),
        ]:
            out.extend(
                classifier.observe(
                    _record(timestamp=base + timedelta(seconds=offset), logger=logger),
                )
            )

        assert len(out) == 1
        assert out[0].logger == "telegram"

    def test_warnings_without_logger_are_ignored(self) -> None:
        classifier = IncidentClassifier(warning_burst_threshold=2)
        out = []
        for _ in range(5):
            out.extend(classifier.observe(_record(logger="")))

        assert out == []


class TestTracebackRule:
    def test_traceback_emitted_when_followup_record_arrives(self) -> None:
        classifier = IncidentClassifier(traceback_followup_s=5.0)
        parent = _record(
            level=LogLevel.ERROR,
            logger="tools.terminal_tool",
            message="Traceback (most recent call last):",
            timestamp=datetime(2026, 5, 12, 0, 12, 17),
        )
        frame = _record(
            level=LogLevel.ERROR,
            logger="",
            message='  File "/path", line 123, in foo',
            timestamp=datetime.min,
            is_continuation=True,
        )
        successor = _record(
            level=LogLevel.ERROR,
            logger="tools.terminal_tool",
            message="next error",
            timestamp=datetime(2026, 5, 12, 0, 12, 30),
        )

        # Parent: emits the ERROR severity incident; the traceback stays open.
        first = classifier.observe(parent)
        assert {i.rule for i in first} == {"error_severity"}

        # Continuation frame: attaches to the open traceback, no emission.
        assert classifier.observe(frame) == []

        # Successor: closes the traceback (rule="traceback") and emits its own
        # error_severity incident.
        out = classifier.observe(successor)
        rules = [i.rule for i in out]
        assert "traceback" in rules
        traceback = next(i for i in out if i.rule == "traceback")
        assert traceback.severity is IncidentSeverity.CRITICAL
        assert frame in traceback.records
        assert parent in traceback.records

    def test_flush_finalizes_pending_traceback(self) -> None:
        classifier = IncidentClassifier()
        parent = _record(
            level=LogLevel.ERROR,
            logger="tools.terminal_tool",
            message="Traceback (most recent call last):",
        )
        classifier.observe(parent)
        flushed = classifier.flush()

        assert len(flushed) == 1
        assert flushed[0].rule == "traceback"

    def test_traceback_finalized_by_deadline(self) -> None:
        classifier = IncidentClassifier(traceback_followup_s=1.0)
        parent = _record(
            level=LogLevel.ERROR,
            logger="tools.terminal_tool",
            message="Traceback (most recent call last):",
            timestamp=datetime(2026, 5, 12, 0, 0, 0),
        )
        classifier.observe(parent)

        # A new record from a *different* logger past the deadline closes
        # the open traceback even though it doesn't match the parent's
        # logger.
        unrelated = _record(
            level=LogLevel.WARNING,
            logger="other.logger",
            message="something",
            timestamp=datetime(2026, 5, 12, 0, 0, 30),
        )
        out = classifier.observe(unrelated)
        assert any(i.rule == "traceback" for i in out)


class TestClassifyAll:
    def test_runs_full_stream_and_flushes(self) -> None:
        records = [
            _record(level=LogLevel.WARNING, timestamp=datetime(2026, 5, 12, 0, 0, i))
            for i in range(5)
        ]
        records.append(
            _record(
                level=LogLevel.ERROR,
                logger="root",
                message="Traceback (most recent call last):",
                timestamp=datetime(2026, 5, 12, 0, 0, 6),
            )
        )

        incidents = classify_all(records)

        rules = [i.rule for i in incidents]
        assert "warning_burst" in rules
        assert "error_severity" in rules
        assert "traceback" in rules


class TestValidation:
    @pytest.mark.parametrize("threshold", [0, 1])
    def test_threshold_below_two_rejected(self, threshold: int) -> None:
        with pytest.raises(ValueError, match="threshold"):
            IncidentClassifier(warning_burst_threshold=threshold)

    def test_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="window"):
            IncidentClassifier(warning_burst_window_s=0)
