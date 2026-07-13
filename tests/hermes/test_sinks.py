"""Tests for :mod:`integrations.hermes.sinks`."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

import pytest

from integrations.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from integrations.hermes.investigation import run_incident_investigation
from integrations.hermes.sinks import TelegramSink, TelegramSinkConfig, make_telegram_sink
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials

_TS = datetime(2026, 5, 12, 0, 0, 0)


# Default test config: run the bridge inline so unit tests are
# deterministic. The pooled path is exercised separately by
# TestPooledBridge to keep its slower/race-sensitive tests scoped.
_INLINE = TelegramSinkConfig(bridge_run_inline=True)


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


class TestSeverityRouting:
    def test_high_incident_triggers_investigation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "root cause: redis is down"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(bridge_calls) == 1
        assert "investigation summary:" in calls[0]["text"]
        assert "root cause: redis is down" in calls[0]["text"]

    def test_critical_incident_triggers_investigation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "root cause: oom kill"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        assert len(bridge_calls) == 1
        assert "root cause: oom kill" in calls[0]["text"]

    def test_medium_incident_skips_investigation_and_marks_notify_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "should not appear"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.MEDIUM, rule="warning_burst"))

        assert bridge_calls == []
        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "notify only" in text

    def test_bridge_returning_none_marks_attempted_no_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operator must be able to distinguish 'no bridge configured'
        from 'bridge ran and returned nothing' — Greptile #1858 P2."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return None

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "investigation: attempted (no summary produced)" in text

    def test_bridge_exception_is_marked_attempted_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bridge exceptions must surface a 'failed' marker on Telegram
        so operators don't conflate them with 'investigation disabled'."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            raise RuntimeError("LLM unreachable")

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        # Must not raise — a broken investigation pipeline cannot block
        # notification delivery.
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(calls) == 1
        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "investigation: attempted (failed" in text

    def test_builtin_investigation_bridge_propagates_pipeline_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``run_incident_investigation`` must not swallow ``run_investigation``
        exceptions — the sink distinguishes failure from \"no summary\"."""

        dispatcher, calls = _dispatcher(monkeypatch)

        def _boom(_alert: dict[str, Any]) -> Any:
            raise RuntimeError("investigation pipeline exploded")

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=lambda incident: run_incident_investigation(incident, _boom),
            config=_INLINE,
        )
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(calls) == 1
        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "investigation: attempted (failed" in text

    def test_high_incident_without_bridge_omits_investigation_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no bridge is configured at all, no investigation block
        is emitted (the markers are reserved for bridge-attempted states)."""
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)
        sink(_incident(severity=IncidentSeverity.HIGH))

        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "investigation: attempted" not in text


class TestPooledBridge:
    """Verify the pooled bridge execution path: timeouts must surface
    as an explicit marker, and the call must not block longer than
    ``bridge_timeout_s`` even when the bridge hangs."""

    def test_bridge_timeout_marks_attempted_timed_out_and_does_not_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_started = threading.Event()
        bridge_release = threading.Event()

        def _slow_bridge(_incident: HermesIncident) -> str | None:
            bridge_started.set()
            # Block until released so the test deterministically hits
            # the timeout path. The future is left running on timeout;
            # we release it at teardown so the worker thread exits.
            bridge_release.wait(timeout=5.0)
            return "too late"

        # 50 ms timeout keeps the test fast while still exercising the
        # pooled (off-thread) code path.
        config = TelegramSinkConfig(bridge_timeout_s=0.05, bridge_workers=1)
        sink = TelegramSink(dispatcher, investigation_bridge=_slow_bridge, config=config)
        try:
            start = time.monotonic()
            sink(_incident(severity=IncidentSeverity.CRITICAL))
            elapsed = time.monotonic() - start

            # Must return well under the bridge's own would-be runtime.
            # Generous upper bound to absorb CI scheduling noise.
            assert elapsed < 1.0, f"sink blocked for {elapsed:.2f}s; expected <1.0s"
            assert bridge_started.is_set(), "bridge worker never started"
            text = calls[0]["text"]
            assert "investigation summary:" not in text
            assert "investigation: attempted (timed out after" in text
            assert "too late" not in text  # late return must be discarded
        finally:
            bridge_release.set()
            sink.close()

    def test_after_close_does_not_run_bridge_inline_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Closing the sink must not route investigations through the inline
        path just because the executor handle was cleared — post-shutdown
        inline calls race in-flight pool workers and block the caller."""
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(inc: HermesIncident) -> str | None:
            bridge_calls.append(inc)
            return "should not run after close"

        config = TelegramSinkConfig(bridge_timeout_s=2.0, bridge_workers=1)
        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=config)
        sink.close()
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert bridge_calls == []
        assert "investigation: skipped (Hermes sink closed" in calls[0]["text"]


class TestSinkClosedInline:
    """``close()`` must suppress bridge calls for the inline path too."""

    def test_after_close_skips_investigation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "nope"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink.close()
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        assert bridge_calls == []
        assert "investigation: skipped (Hermes sink closed" in calls[0]["text"]


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

    def test_make_telegram_sink_factory_returns_callable_with_bridge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "RCA"

        sink = make_telegram_sink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert callable(sink)
        assert len(calls) == 1
        assert len(bridge_calls) == 1

    def test_run_bridge_in_pool_returns_sink_closed_when_executor_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_run_bridge_in_pool must handle a None executor gracefully instead
        of raising AssertionError (which would crash under optimised bytecode or
        after a concurrent close())."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return "should not be called"

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=_bridge,
            config=TelegramSinkConfig(bridge_run_inline=False, bridge_workers=1),
        )
        # Manually null the executor to simulate the race between close() and
        # an in-flight _run_bridge_in_pool call.
        sink._bridge_executor = None  # type: ignore[attr-defined]

        # Calling the pooled bridge path directly must return sink_closed, not raise.
        result = sink._run_bridge_in_pool(_bridge, _incident(severity=IncidentSeverity.HIGH))  # type: ignore[attr-defined]
        assert result.state.value == "sink_closed"

    def test_submit_runtime_error_still_dispatches_telegram(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``executor.submit`` raises (pool shut down), investigation is skipped
        but the Telegram notification must still be sent — ``__call__`` must not
        abort before ``dispatch``."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return "RCA"

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=_bridge,
            config=TelegramSinkConfig(bridge_run_inline=False, bridge_workers=1),
        )
        ex = sink._bridge_executor
        assert ex is not None

        def _boom_submit(*_a: object, **_kw: object) -> None:
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

        monkeypatch.setattr(ex, "submit", _boom_submit)

        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(calls) == 1
        text = calls[0]["text"]
        assert "sink closed" in text.lower() or "skipped" in text.lower()

    def test_cancelled_future_shows_sink_closed_not_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """future.result() raises CancelledError when shutdown(cancel_futures=True)
        cancels an in-flight future.  This must surface as 'sink_closed' in the
        Telegram body — not 'attempted (failed)' — because the cancellation is
        the result of an orderly close(), not an investigation error."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return "RCA"

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=_bridge,
            config=TelegramSinkConfig(bridge_run_inline=False, bridge_workers=1),
        )

        # Simulate a future that was cancelled by executor.shutdown(cancel_futures=True)
        from concurrent.futures import Future

        cancelled_future: Future[str | None] = Future()
        cancelled_future.cancel()

        ex = sink._bridge_executor  # type: ignore[attr-defined]
        assert ex is not None

        def _submit_cancelled(*_a: object, **_kw: object) -> Future[str | None]:
            return cancelled_future

        monkeypatch.setattr(ex, "submit", _submit_cancelled)

        result = sink._run_bridge_in_pool(  # type: ignore[attr-defined]
            _bridge, _incident(severity=IncidentSeverity.HIGH)
        )
        assert result.state.value == "sink_closed", (
            f"CancelledError should yield sink_closed, got: {result.state.value}"
        )
        sink.close()


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


class TestSummaryTruncation:
    """A successful investigation summary is inlined into the message body but
    must respect ``max_summary_chars`` so one verbose RCA cannot blow past the
    Telegram limit and crowd out the incident metadata above it."""

    def test_long_summary_is_truncated_with_ellipsis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        long_summary = "root cause: " + ("y" * 5000)

        def _bridge(_incident: HermesIncident) -> str | None:
            return long_summary

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=_bridge,
            config=TelegramSinkConfig(bridge_run_inline=True, max_summary_chars=100),
        )
        sink(_incident(severity=IncidentSeverity.HIGH))

        text = calls[0]["text"]
        assert "investigation summary:" in text
        assert long_summary not in text
        assert "…" in text

    def test_empty_string_summary_is_treated_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty-string return is the documented ``None``-equivalent of the
        bridge contract and must surface the EMPTY marker, not a blank block."""
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return ""

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "investigation: attempted (no summary produced)" in text

    def test_summary_is_stripped_before_inlining(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return "\n  root cause: disk full  \n"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.HIGH))

        text = calls[0]["text"]
        assert "investigation summary:\nroot cause: disk full" in text


class TestSeverityGate:
    """Only HIGH/CRITICAL run the bridge. LOW is silent (no marker at all);
    MEDIUM carries the notify-only marker. These cases fill the gap between
    the two already-covered severities."""

    def test_low_severity_never_runs_bridge_and_emits_no_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "should never run"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=IncidentSeverity.LOW, rule="info_noise"))

        assert bridge_calls == []
        text = calls[0]["text"]
        assert "investigation" not in text.lower()
        assert "notify only" not in text

    @pytest.mark.parametrize(
        ("severity", "should_investigate"),
        [
            (IncidentSeverity.LOW, False),
            (IncidentSeverity.MEDIUM, False),
            (IncidentSeverity.HIGH, True),
            (IncidentSeverity.CRITICAL, True),
        ],
    )
    def test_investigation_gate_matches_severity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        severity: IncidentSeverity,
        should_investigate: bool,
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "rca"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge, config=_INLINE)
        sink(_incident(severity=severity))

        assert bool(bridge_calls) is should_investigate
        assert ("investigation summary:" in calls[0]["text"]) is should_investigate


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

        def _bridge(_incident: HermesIncident) -> str | None:
            return "rca"

        sink = TelegramSink(
            dispatcher,
            investigation_bridge=_bridge,
            config=TelegramSinkConfig(bridge_workers=1),
        )
        # Multiple close() calls must not raise (SIGTERM handlers may double-fire).
        sink.close()
        sink.close()

    def test_close_without_bridge_is_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, _ = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)  # no bridge → no executor ever created
        sink.close()
