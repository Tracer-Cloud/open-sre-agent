"""Tests for :mod:`app.hermes.poller`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import app.hermes.poller as hermes_poller
from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import LogLevel
from tests.utils.hermes_logs_helper import hermes_log_fixture

_LINES_BURST = [
    "2026-05-12 00:00:00,000 WARNING gateway.platforms.telegram: polling conflict (1/3), retrying",
    "2026-05-12 00:00:10,000 WARNING gateway.platforms.telegram: polling conflict (2/3), retrying",
    "2026-05-12 00:00:20,000 WARNING gateway.platforms.telegram: polling conflict (3/3), retrying",
    "2026-05-12 00:00:30,000 WARNING gateway.platforms.telegram: polling conflict (1/3), retrying",
    "2026-05-12 00:00:40,000 WARNING gateway.platforms.telegram: polling conflict (2/3), retrying",
]


class TestCursorTokenRoundTrip:
    def test_token_round_trip_preserves_all_fields(self) -> None:
        cursor = hermes_poller.HermesLogCursor(path="/tmp/x.log", device=42, inode=99, offset=1024)
        restored = hermes_poller.HermesLogCursor.from_token(cursor.to_token())
        assert restored == cursor

    def test_token_round_trip_supports_paths_with_at_sign(self) -> None:
        # The token uses '@' as a separator; a path containing '@' must
        # still round-trip because the parser greedy-matches the path
        # after the final '@'.
        cursor = hermes_poller.HermesLogCursor(
            path="/var/log/user@host.log", device=1, inode=2, offset=3
        )
        restored = hermes_poller.HermesLogCursor.from_token(cursor.to_token())
        assert restored == cursor

    def test_malformed_token_raises(self) -> None:
        with pytest.raises(ValueError):
            hermes_poller.HermesLogCursor.from_token("not-a-cursor")


class TestValidateExpectedLogPath:
    def test_accepts_matching_paths(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("x\n", encoding="utf-8")
        cursor = hermes_poller.HermesLogCursor(path=str(log), device=1, inode=2, offset=3)
        cursor.validate_expected_log_path(log)
        cursor.validate_expected_log_path(str(log))

    def test_rejects_path_mismatch(self, tmp_path: Path) -> None:
        a = tmp_path / "a.log"
        b = tmp_path / "b.log"
        a.write_text("x\n", encoding="utf-8")
        b.write_text("y\n", encoding="utf-8")
        cursor = hermes_poller.HermesLogCursor(path=str(a), device=0, inode=0, offset=0)
        with pytest.raises(ValueError, match="does not refer"):
            cursor.validate_expected_log_path(b)


class TestPollerBasics:
    def test_first_poll_on_empty_file_returns_no_records(self, tmp_path: Path) -> None:
        with hermes_log_fixture(tmp_path) as fixture:
            poll = fixture.poll_once()
            assert poll.records == ()
            assert poll.incidents == ()
            assert not poll.rotation_detected

    def test_only_new_lines_are_returned_on_subsequent_poll(self, tmp_path: Path) -> None:
        with hermes_log_fixture(tmp_path) as fixture:
            fixture.write_line(_LINES_BURST[0])
            first = fixture.poll_once()
            assert len(first.records) == 1
            assert first.records[0].logger == "gateway.platforms.telegram"

            # Add three more lines and verify the second poll yields
            # exactly those three — NOT the original one again.
            for line in _LINES_BURST[1:4]:
                fixture.write_line(line)
            second = fixture.poll_once()
            assert len(second.records) == 3
            assert all(r.logger == "gateway.platforms.telegram" for r in second.records)

    def test_classifier_state_persists_across_polls(self, tmp_path: Path) -> None:
        """Threshold-based incidents must fire across poll boundaries —
        otherwise a fast tailer that polls between every two writes
        would never accumulate enough records to emit a burst."""
        with hermes_log_fixture(tmp_path) as fixture:
            fixture.classifier = IncidentClassifier(
                warning_burst_threshold=3, warning_burst_window_s=60.0
            )
            # Write two warnings + poll → no incident yet
            fixture.write_line(_LINES_BURST[0])
            fixture.write_line(_LINES_BURST[1])
            assert fixture.poll_once().incidents == ()
            # Write the third → burst should fire on this poll
            fixture.write_line(_LINES_BURST[2])
            poll = fixture.poll_once()
            assert len(poll.incidents) == 1
            assert poll.incidents[0].rule == "warning_burst"


class TestLevelFilter:
    def test_level_filter_drops_lines_but_still_classifies_them(self, tmp_path: Path) -> None:
        """A level filter must not prevent warning_burst from firing —
        the classifier still observes filtered records."""
        with hermes_log_fixture(tmp_path) as fixture:
            fixture.classifier = IncidentClassifier(
                warning_burst_threshold=3, warning_burst_window_s=60.0
            )
            for line in _LINES_BURST[:3]:
                fixture.write_line(line)

            poll = fixture.poll_once(level_filter=frozenset({LogLevel.ERROR}))
            assert poll.records == (), "WARNING records should be dropped from response"
            assert len(poll.incidents) == 1, "but the classifier should still emit the burst"
            assert poll.incidents[0].rule == "warning_burst"


class TestSinceFilter:
    def test_continuations_are_filtered_with_their_parent(self, tmp_path: Path) -> None:
        p = tmp_path / "errs.log"
        p.write_text(
            "2026-05-12 00:00:00,000 ERROR x: Traceback (most recent call last):\n"
            '  File "/x", line 1, in foo\n'
            "2026-05-12 00:00:30,000 ERROR x: later\n",
            encoding="utf-8",
        )
        since = datetime(2026, 5, 12, 0, 0, 10)
        poll = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            classifier=IncidentClassifier(),
            since=since,
        )
        assert [r.message for r in poll.records] == ["later"]
        assert all(not r.is_continuation for r in poll.records)

    def test_continuations_survive_when_parent_passes_since(self, tmp_path: Path) -> None:
        p = tmp_path / "errs.log"
        p.write_text(
            "2026-05-12 00:00:20,000 ERROR x: Traceback (most recent call last):\n"
            '  File "/x", line 1, in foo\n',
            encoding="utf-8",
        )
        since = datetime(2026, 5, 12, 0, 0, 10)
        poll = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            classifier=IncidentClassifier(),
            since=since,
        )
        assert len(poll.records) == 2
        assert poll.records[0].is_continuation is False
        assert poll.records[1].is_continuation is True

    def test_interleaved_loggers_continuations_follow_own_parent(self, tmp_path: Path) -> None:
        """Regression: scalar parent_passes_since was overwritten by each
        non-continuation record regardless of logger.  When logger-B (filtered)
        appears between logger-A's header and a later logger-A record, the
        *next* non-header record after logger-B inherits logger-B's decision.

        Real Python logging emits each exception traceback atomically, so the
        realistic interleaving is: a non-continuation from a filtered logger
        appearing BETWEEN two non-continuation records of a passing logger.
        The second logger-A record falls back to logger-A's own saved decision,
        not logger-B's more recent one.

        Layout (since = 10s):
          t=20s  logger-A header  → passes (>= since)
          t=05s  logger-B header  → filtered (< since)   ← would poison scalar
          t=25s  logger-A record  → should pass (logger-A's own history)
        """
        p = tmp_path / "interleaved.log"
        p.write_text(
            "2026-05-12 00:00:20,000 ERROR logger-A: first message\n"
            "2026-05-12 00:00:05,000 ERROR logger-B: old message\n"
            "2026-05-12 00:00:25,000 ERROR logger-A: second message\n",
            encoding="utf-8",
        )
        since = datetime(2026, 5, 12, 0, 0, 10)
        poll = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            classifier=IncidentClassifier(),
            since=since,
        )
        messages = [r.message for r in poll.records if not r.is_continuation]
        # logger-B (t=05s) is before since, logger-A records (t=20s, t=25s) pass
        assert "first message" in messages, "logger-A t=20s must pass since filter"
        assert "second message" in messages, "logger-A t=25s must pass since filter"
        assert "old message" not in messages, "logger-B t=05s must be excluded"

    def test_continuation_follows_traceback_header_not_interleaved_logger(
        self, tmp_path: Path
    ) -> None:
        """Regression: a continuation frame must inherit the filter decision of
        its own parent header, not a different logger that wrote between the
        header and the continuation.

        Layout (since = 10s):
          t=20s  logger-A: Traceback …  → passes
          t=05s  logger-B: plain line   → filtered  ← old code poisons scalar
          continuation frame            → must still pass (parent was logger-A)
        """
        p = tmp_path / "interleaved2.log"
        p.write_text(
            "2026-05-12 00:00:20,000 ERROR logger-A: Traceback (most recent call last):\n"
            "2026-05-12 00:00:05,000 ERROR logger-B: unrelated\n"
            '  File "/a.py", line 1, in foo\n',
            encoding="utf-8",
        )
        since = datetime(2026, 5, 12, 0, 0, 10)
        poll = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            classifier=IncidentClassifier(),
            since=since,
        )
        continuations = [r for r in poll.records if r.is_continuation]
        assert len(continuations) >= 1, (
            "continuation after logger-A header must not be dropped because "
            "logger-B (filtered) appeared between header and continuation"
        )

    def test_filtered_pre_since_line_before_traceback_does_not_drop_frames(
        self, tmp_path: Path
    ) -> None:
        """A filtered non-traceback line before a passing Traceback must not sit
        in ``since_queue`` and poison the first frame's ``passes_since``."""
        p = tmp_path / "since_traceback.log"
        p.write_text(
            "2026-05-12 00:00:05,000 ERROR stale: noise before since window\n"
            "2026-05-12 00:00:20,000 ERROR logger: Traceback (most recent call last):\n"
            '  File "/x.py", line 1, in y\n',
            encoding="utf-8",
        )
        since = datetime(2026, 5, 12, 0, 0, 10)
        poll = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            classifier=IncidentClassifier(),
            since=since,
        )
        assert any(r.is_continuation and "x.py" in r.message for r in poll.records), (
            "traceback frame must appear in returned records after a filtered pre-since line"
        )


class TestRotationAndTruncation:
    def test_rotation_resets_offset_and_flags_detection(self, tmp_path: Path) -> None:
        """logrotate-style rotation: the original file is renamed
        (inode survives, attached to the rotated copy) and a fresh
        file with a NEW inode is created at the original path."""
        with hermes_log_fixture(tmp_path) as fixture:
            fixture.write_line(_LINES_BURST[0])
            first = fixture.poll_once()
            assert len(first.records) == 1

            # Move the current file aside (preserves its inode on the
            # rotated copy) and create a fresh file at the original
            # path. ``os.rename`` is what logrotate actually does.
            rotated = fixture.path.with_suffix(".log.1")
            import os as _os

            _os.rename(fixture.path, rotated)
            fixture.path.touch()
            fixture.write_line(_LINES_BURST[1])

            second = fixture.poll_once()
            assert second.rotation_detected, "poll did not detect inode change after rotation"
            assert len(second.records) == 1
            assert "(2/3)" in second.records[0].message

    def test_truncation_to_shorter_file_rewinds(self, tmp_path: Path) -> None:
        with hermes_log_fixture(tmp_path) as fixture:
            for line in _LINES_BURST[:3]:
                fixture.write_line(line)
            fixture.poll_once()

            # Truncate in place (keeps the same inode) and write a
            # single replacement line. The poller should treat this
            # as 'file shrank below my offset' and rewind.
            fixture.path.write_text("", encoding="utf-8")
            fixture.write_line(_LINES_BURST[4])

            second = fixture.poll_once()
            assert second.rotation_detected, "truncation should trigger rewind"
            assert len(second.records) == 1
            assert "(2/3)" in second.records[0].message  # the [4] line


class TestMaxLines:
    def test_max_lines_caps_response_and_reports_truncation(self, tmp_path: Path) -> None:
        with hermes_log_fixture(tmp_path) as fixture:
            for line in _LINES_BURST:
                fixture.write_line(line)
            poll = fixture.poll_once(max_lines=2)
            assert len(poll.records) == 2
            # truncated_lines must be ≥1: at least one line was not returned.
            assert poll.truncated_lines >= 1
            # The cursor must be rewound before the first uncapped line so
            # the caller can drain the rest on the next poll.
            assert poll.cursor.offset < fixture.path.stat().st_size

    def test_max_lines_cursor_resumes_from_cap_point(self, tmp_path: Path) -> None:
        with hermes_log_fixture(tmp_path) as fixture:
            for line in _LINES_BURST:
                fixture.write_line(line)
            first = fixture.poll_once(max_lines=2)
            assert len(first.records) == 2
            # Second poll must pick up the remaining lines.
            second = fixture.poll_once(max_lines=10)
            assert len(second.records) == 3
            assert second.truncated_lines == 0

    def test_max_lines_boundary_line_not_double_classified_across_polls(
        self, tmp_path: Path
    ) -> None:
        """The line deferred by max_lines must not be classifier.observe'd before
        rewind — a fresh classifier on the next poll would emit duplicate incidents."""
        p = tmp_path / "errs.log"
        p.write_text(
            "2026-05-12 00:00:00,000 ERROR a: one\n"
            "2026-05-12 00:00:01,000 ERROR b: two\n"
            "2026-05-12 00:00:02,000 ERROR c: three\n",
            encoding="utf-8",
        )
        first = hermes_poller.poll_hermes_logs(
            p,
            hermes_poller.HermesLogCursor.at_start(p),
            max_lines=2,
            classifier=IncidentClassifier(),
        )
        assert len(first.records) == 2
        second = hermes_poller.poll_hermes_logs(
            p,
            first.cursor,
            max_lines=10,
            classifier=IncidentClassifier(),
        )
        assert len(second.records) == 1
        err_incidents = [
            i for i in first.incidents + second.incidents if i.rule == "error_severity"
        ]
        assert len(err_incidents) == 3
        assert len({i.fingerprint for i in err_incidents}) == 3


class TestMissingFile:
    def test_missing_file_returns_empty_at_start_cursor(self, tmp_path: Path) -> None:
        """Polling a path that doesn't exist yet must not raise — the
        opensre hermes watch command relies on this so it can start
        before logs/errors.log appears."""
        ghost = tmp_path / "does-not-exist.log"
        poll = hermes_poller.poll_hermes_logs(ghost, hermes_poller.HermesLogCursor.at_start(ghost))
        assert poll.records == ()
        assert poll.cursor.offset == 0


class TestByteBudgetLineBoundary:
    def test_budget_stops_at_line_boundary_and_resumes_next_poll(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Do not partially consume a line when the byte budget runs out mid-read.

        Cursor must rewind before that line so the next poll reads it entirely.
        """
        p = tmp_path / "errs.log"
        line1 = "2026-05-12 00:00:00,000 INFO one: aaa\n"
        line2 = "2026-05-12 00:00:01,000 INFO two: bbb\n"
        p.write_text(line1 + line2, encoding="utf-8")
        b1 = len(line1.encode("utf-8"))
        monkeypatch.setattr(hermes_poller, "_DEFAULT_MAX_BYTES", b1 + 1)

        first = hermes_poller.poll_hermes_logs(
            p, hermes_poller.HermesLogCursor.at_start(p), classifier=IncidentClassifier()
        )
        assert len(first.records) == 1
        assert first.records[0].message.endswith("aaa")
        assert first.cursor.offset == b1
        assert first.truncated_lines >= 1, (
            "byte-budget stop with unread bytes must set truncated_lines so has_more is true"
        )

        second = hermes_poller.poll_hermes_logs(p, first.cursor, classifier=IncidentClassifier())
        assert len(second.records) == 1
        assert second.records[0].message.endswith("bbb")


class TestPollUntil:
    def test_poll_until_satisfies_predicate(self, tmp_path: Path) -> None:
        """End-to-end test of the helper's poll_until loop on a live
        write pattern (no actual threading — write all then poll)."""
        with hermes_log_fixture(tmp_path) as fixture:
            fixture.classifier = IncidentClassifier(
                warning_burst_threshold=3, warning_burst_window_s=60.0
            )
            fixture.write_lines(_LINES_BURST[:3])
            satisfied = fixture.poll_until(
                lambda f: any(i.rule == "warning_burst" for i in f.accumulated_incidents),
                budget_s=1.0,
            )
            assert satisfied
            assert fixture.rule_counts().get("warning_burst") == 1
