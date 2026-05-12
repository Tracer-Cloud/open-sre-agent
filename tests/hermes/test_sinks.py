"""Tests for :mod:`app.hermes.sinks`."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

import pytest

from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from app.hermes.investigation import run_incident_investigation
from app.hermes.sinks import TelegramSink, TelegramSinkConfig, make_telegram_sink
from app.watch_dog.alarms import AlarmCredentials, AlarmDispatcher

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

    def _fake_post(chat_id: str, text: str, bot_token: str) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    monkeypatch.setattr("app.watch_dog.alarms.post_telegram_message", _fake_post)
    return calls


def _dispatcher(monkeypatch: pytest.MonkeyPatch) -> tuple[AlarmDispatcher, list[dict[str, Any]]]:
    calls = _capture_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok", chat_id="chat-1")
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

        def _boom(**_kwargs: Any) -> Any:
            raise RuntimeError("langgraph exploded")

        monkeypatch.setattr("app.pipeline.runners.run_investigation", _boom)
        sink = TelegramSink(
            dispatcher,
            investigation_bridge=run_incident_investigation,
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
        assert "sink_closed" in result.state

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
        assert result.state == "sink_closed", (
            f"CancelledError should yield sink_closed, got: {result.state}"
        )
        sink.close()
