"""Tests for :mod:`integrations.hermes.sinks`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from integrations.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from integrations.hermes.sinks import TelegramSink, TelegramSinkConfig, make_telegram_sink
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials

_TS = datetime(2026, 5, 12, 0, 0, 0)


def _record(level: LogLevel, logger_name: str, message: str) -> LogRecord:
    raw = f"{_TS.isoformat()} {level.value} {logger_name}: {message}"
    return LogRecord(timestamp=_TS, level=level, logger=logger_name, message=message, raw=raw)


def _incident(
    *,
    rule: str = "error_severity",
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    logger_name: str = "gateway.platforms.telegram",
    title: str = "ERROR from gateway.platforms.telegram",
    fingerprint: str = "deadbeef00000001",
    records: tuple[LogRecord, ...] | None = None,
    run_id: str | None = None,
) -> HermesIncident:
    if records is None:
        records = (_record(LogLevel.ERROR, logger_name, "boom"),)
    return HermesIncident(
        rule=rule,
        severity=severity,
        title=title,
        detected_at=_TS,
        logger=logger_name,
        fingerprint=fingerprint,
        records=records,
        run_id=run_id,
    )


def _capture_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(
        chat_id: str,
        text: str,
        bot_token: str,
        parse_mode: str = "",
        reply_to_message_id: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "bot_token": bot_token,
                "parse_mode": parse_mode,
                "reply_to_message_id": reply_to_message_id,
                "reply_markup": reply_markup,
            }
        )
        return True, "", "1"

    monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _fake_post)
    return calls


def _dispatcher(monkeypatch: pytest.MonkeyPatch) -> tuple[AlarmDispatcher, list[dict[str, Any]]]:
    calls = _capture_telegram(monkeypatch)
    creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
    return AlarmDispatcher(creds, cooldown_seconds=300.0), calls


class TestFormatting:
    def test_message_contains_core_incident_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(run_id="run-xyz"))

        assert len(calls) == 1
        text = calls[0]["text"]
        # Each field the operator scans for at a glance.
        for needle in (
            "Hermes incident: ERROR from gateway.platforms.telegram",
            "severity: HIGH",
            "rule: error_severity",
            "logger: gateway.platforms.telegram",
            "fingerprint: deadbeef00000001",
            "run_id: run-xyz",
            "recent log records:",
        ):
            assert needle in text, f"missing {needle!r} in:\n{text}"

    def test_message_truncates_long_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_record_chars=50))

        long_msg = "x" * 500
        sink(_incident(records=(_record(LogLevel.ERROR, "noisy", long_msg),)))

        text = calls[0]["text"]
        # The raw record line should have been collapsed with the
        # ellipsis suffix, not pasted in full.
        assert long_msg not in text
        assert "…" in text

    def test_message_inlines_at_most_max_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_inlined_records=2))

        records = tuple(_record(LogLevel.ERROR, "noisy", f"line-{i}") for i in range(5))
        sink(_incident(records=records))

        text = calls[0]["text"]
        assert "line-0" in text
        assert "line-1" in text
        assert "line-4" not in text  # trimmed
        assert "3 more records omitted" in text


class TestDispatcherIntegration:
    def test_duplicate_fingerprint_is_suppressed_by_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        # Freeze monotonic time so the second dispatch falls inside the
        # default 300-second cooldown.
        monkeypatch.setattr(AlarmDispatcher, "_now", staticmethod(lambda: 1000.0))

        sink = TelegramSink(dispatcher)
        sink(_incident(fingerprint="same-fp"))
        sink(_incident(fingerprint="same-fp"))

        assert len(calls) == 1

    def test_different_fingerprints_both_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(fingerprint="fp-a"))
        sink(_incident(fingerprint="fp-b"))

        assert len(calls) == 2

    def test_make_telegram_sink_factory_returns_callable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)

        sink = make_telegram_sink(dispatcher)
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert callable(sink)
        assert len(calls) == 1


class TestDeliveryTransport:
    """The sink's only egress is ``post_telegram_message`` via
    :class:`AlarmDispatcher`. These tests pin the transport contract: the
    resolved credentials and parse mode must reach the wire unchanged, and a
    failing or raising transport must never propagate out of the sink."""

    def test_resolved_credentials_reach_the_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _capture_telegram(monkeypatch)
        creds = TelegramCredentials(bot_token="secret-token", chat_id="chat-42")
        dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
        sink = TelegramSink(dispatcher)

        sink(_incident())

        assert len(calls) == 1
        assert calls[0]["chat_id"] == "chat-42"
        assert calls[0]["bot_token"] == "secret-token"

    def test_html_parse_mode_is_forwarded_to_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _capture_telegram(monkeypatch)
        creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
        dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0, parse_mode="HTML")
        sink = TelegramSink(dispatcher)

        sink(_incident())

        assert calls[0]["parse_mode"] == "HTML"

    def test_default_parse_mode_is_plain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident())

        assert calls[0]["parse_mode"] == ""

    def test_transport_returning_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``(False, error, "")`` transport result is an *expected* delivery
        failure — the sink must swallow it, not surface it to the agent."""

        def _failing_post(*_a: Any, **_kw: Any) -> tuple[bool, str, str]:
            return False, "telegram: 502 bad gateway", ""

        monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _failing_post)
        creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
        dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
        sink = TelegramSink(dispatcher)

        # Must not raise even though delivery failed.
        sink(_incident(severity=IncidentSeverity.HIGH))

    def test_transport_raising_is_swallowed_by_sink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A transport that *raises* (e.g. a socket error escaping the HTTP
        layer) must not crash the sink — Hermes delivery is best-effort."""

        def _raising_post(*_a: Any, **_kw: Any) -> tuple[bool, str, str]:
            raise ConnectionError("connection reset by peer")

        monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _raising_post)
        creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
        dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
        sink = TelegramSink(dispatcher)

        # AlarmDispatcher.dispatch catches the transport exception; the sink
        # must complete cleanly regardless.
        sink(_incident(severity=IncidentSeverity.CRITICAL))


class TestRecordFormatting:
    """Record-block rendering edges that the operator sees directly."""

    def test_no_records_omits_recent_log_records_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(records=()))

        text = calls[0]["text"]
        assert "recent log records:" not in text
        # Core metadata is still present.
        assert "Hermes incident:" in text

    def test_single_omitted_record_uses_singular_wording(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_inlined_records=2))

        records = tuple(_record(LogLevel.ERROR, "noisy", f"line-{i}") for i in range(3))
        sink(_incident(records=records))

        text = calls[0]["text"]
        assert "1 more record omitted" in text
        assert "records omitted" not in text  # singular, not plural

    def test_run_id_absent_omits_run_id_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(run_id=None))

        assert "run_id:" not in calls[0]["text"]


class TestTruncationBoundary:
    """Truncation boundary behaviour, pinned through the public sink API
    (``max_record_chars``) rather than the private ``_truncate`` helper: a
    record exactly at the limit is untouched, one char over collapses to
    ``limit`` chars with a trailing ellipsis."""

    def test_record_exactly_at_limit_is_not_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        # raw = "<iso> ERROR exact: <msg>"; pin the limit to that exact length.
        record = _record(LogLevel.ERROR, "exact", "boundary")
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_record_chars=len(record.raw)))

        sink(_incident(records=(record,)))

        text = calls[0]["text"]
        assert record.raw in text
        assert "…" not in text

    def test_record_one_over_limit_collapses_to_limit_with_ellipsis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        record = _record(LogLevel.ERROR, "over", "boundary")
        limit = len(record.raw) - 1
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_record_chars=limit))

        sink(_incident(records=(record,)))

        text = calls[0]["text"]
        assert record.raw not in text
        # The trimmed line is exactly `limit` chars ending in the ellipsis.
        assert record.raw[: limit - 1] + "…" in text


class TestCloseIdempotency:
    def test_close_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, _ = _dispatcher(monkeypatch)

        sink = TelegramSink(dispatcher)
        # Multiple close() calls must not raise (SIGTERM handlers may double-fire).
        sink.close()
        sink.close()

    def test_sink_still_dispatches_after_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close() is retained as a uniform-host no-op; delivery keeps working."""
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)
        sink.close()

        sink(_incident())

        assert len(calls) == 1
