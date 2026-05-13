"""Tests for IncidentCorrelator and CorrelatingSink."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.hermes.correlating_sink import CorrelatingSink
from app.hermes.correlator import (
    DEFAULT_DEDUP_WINDOW_S,
    IncidentCorrelator,
    RouteDestination,
    correlate_all,
    default_routing_matrix,
)
from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord


def _record(seconds: int = 0) -> LogRecord:
    return LogRecord(
        timestamp=datetime(2026, 5, 12, 12, 0, 0) + timedelta(seconds=seconds),
        level=LogLevel.ERROR,
        logger="hermes.agent",
        message="boom",
        raw="ERROR hermes.agent: boom",
    )


def _incident(
    *,
    rule: str = "error_severity",
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    fingerprint: str = "fp-1",
    seconds: int = 0,
) -> HermesIncident:
    return HermesIncident(
        rule=rule,
        severity=severity,
        title=f"{severity.value} from hermes.agent",
        detected_at=datetime(2026, 5, 12, 12, 0, 0) + timedelta(seconds=seconds),
        logger="hermes.agent",
        fingerprint=fingerprint,
        records=(_record(seconds=seconds),),
    )


class TestCorrelatorDedup:
    def test_first_incident_always_delivered(self) -> None:
        corr = IncidentCorrelator()
        decision = corr.correlate(_incident())
        assert not decision.suppressed
        assert decision.repeat_count == 1
        assert decision.escalated_from is None

    def test_second_within_dedup_window_is_suppressed(self) -> None:
        corr = IncidentCorrelator()
        corr.correlate(_incident(seconds=0))
        decision = corr.correlate(_incident(seconds=10))
        assert decision.suppressed
        assert decision.destination is RouteDestination.DROP

    def test_after_dedup_window_delivers_again(self) -> None:
        corr = IncidentCorrelator(dedup_window_s=30, escalation_window_s=15)
        corr.correlate(_incident(seconds=0))
        decision = corr.correlate(_incident(seconds=60))
        assert not decision.suppressed
        # The second incident is on its own in the escalation window.
        assert decision.repeat_count == 1

    def test_different_fingerprints_do_not_dedupe(self) -> None:
        corr = IncidentCorrelator()
        corr.correlate(_incident(fingerprint="a"))
        decision = corr.correlate(_incident(fingerprint="b"))
        assert not decision.suppressed

    def test_idle_fingerprint_evicted_after_long_gap(self) -> None:
        corr = IncidentCorrelator(dedup_window_s=60, escalation_window_s=60, escalation_threshold=3)
        corr.correlate(_incident(fingerprint="a", seconds=0))
        corr.correlate(_incident(fingerprint="b", seconds=800))
        d = corr.correlate(_incident(fingerprint="a", seconds=801))
        assert not d.suppressed
        assert d.repeat_count == 1


class TestCorrelatorEscalation:
    def test_escalates_after_threshold(self) -> None:
        corr = IncidentCorrelator(dedup_window_s=0, escalation_window_s=60, escalation_threshold=3)
        d1 = corr.correlate(_incident(seconds=0))
        d2 = corr.correlate(_incident(seconds=10))
        d3 = corr.correlate(_incident(seconds=20))
        assert d1.escalated_from is None
        assert d2.escalated_from is None
        assert d3.escalated_from is IncidentSeverity.HIGH
        assert d3.deliver.severity is IncidentSeverity.CRITICAL
        assert "ESCALATED" in d3.deliver.title

    def test_critical_does_not_escalate_further(self) -> None:
        corr = IncidentCorrelator(dedup_window_s=0, escalation_window_s=60, escalation_threshold=2)
        corr.correlate(_incident(severity=IncidentSeverity.CRITICAL))
        d2 = corr.correlate(_incident(severity=IncidentSeverity.CRITICAL, seconds=5))
        # Repeat count triggers escalation logic but severity is already top.
        assert d2.deliver.severity is IncidentSeverity.CRITICAL
        assert d2.escalated_from is None

    def test_escalation_breaks_through_dedup(self) -> None:
        # Dedup window large; escalation threshold low → escalated incidents
        # should still be delivered.
        corr = IncidentCorrelator(
            dedup_window_s=300, escalation_window_s=60, escalation_threshold=3
        )
        corr.correlate(_incident(seconds=0))
        corr.correlate(_incident(seconds=10))
        decision = corr.correlate(_incident(seconds=20))
        assert not decision.suppressed
        assert decision.escalated_from is IncidentSeverity.HIGH


class TestCorrelatorRouting:
    def test_default_matrix_routes_crash_loop_to_pager(self) -> None:
        corr = IncidentCorrelator()
        decision = corr.correlate(_incident(rule="crash_loop"))
        assert decision.destination is RouteDestination.PAGER

    def test_unknown_rule_high_severity_goes_to_telegram(self) -> None:
        corr = IncidentCorrelator()
        decision = corr.correlate(_incident(rule="unknown_rule"))
        assert decision.destination is RouteDestination.TELEGRAM

    def test_unknown_rule_medium_drops(self) -> None:
        corr = IncidentCorrelator()
        decision = corr.correlate(_incident(rule="unknown_rule", severity=IncidentSeverity.MEDIUM))
        assert decision.destination is RouteDestination.DROP

    def test_escalation_to_critical_promotes_telegram_to_pager(self) -> None:
        corr = IncidentCorrelator(
            dedup_window_s=0,
            escalation_window_s=60,
            escalation_threshold=2,
            routing_matrix={"warning_burst": RouteDestination.TELEGRAM},
        )
        corr.correlate(
            _incident(
                rule="warning_burst",
                severity=IncidentSeverity.HIGH,
                fingerprint="burst",
            )
        )
        d2 = corr.correlate(
            _incident(
                rule="warning_burst",
                severity=IncidentSeverity.HIGH,
                fingerprint="burst",
                seconds=10,
            )
        )
        assert d2.escalated_from is IncidentSeverity.HIGH
        # Escalated to CRITICAL → routing promoted from TELEGRAM to PAGER.
        assert d2.destination is RouteDestination.PAGER


class TestCorrelatorValidation:
    def test_rejects_invalid_dedup_window(self) -> None:
        with pytest.raises(ValueError):
            IncidentCorrelator(dedup_window_s=-1)

    def test_rejects_zero_escalation_window(self) -> None:
        with pytest.raises(ValueError):
            IncidentCorrelator(escalation_window_s=0)

    def test_rejects_low_escalation_threshold(self) -> None:
        with pytest.raises(ValueError):
            IncidentCorrelator(escalation_threshold=1)


class TestCorrelateAllAndDefaults:
    def test_correlate_all_batch(self) -> None:
        corr = IncidentCorrelator()
        decisions = correlate_all(
            corr,
            [
                _incident(fingerprint="a"),
                _incident(fingerprint="b"),
                _incident(fingerprint="a", seconds=5),
            ],
        )
        assert len(decisions) == 3
        assert decisions[2].suppressed  # dedup'd

    def test_default_routing_matrix_has_expected_rules(self) -> None:
        matrix = default_routing_matrix()
        assert matrix["crash_loop"] is RouteDestination.PAGER
        assert matrix["disk_full"] is RouteDestination.PAGER
        assert matrix["oom_killed"] is RouteDestination.TELEGRAM_WITH_RCA
        assert matrix["rate_limit"] is RouteDestination.TELEGRAM


class TestCorrelatingSink:
    def test_delivers_to_routed_sink(self) -> None:
        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={RouteDestination.TELEGRAM_WITH_RCA: delivered.append},
        )
        sink(_incident())
        assert len(delivered) == 1

    def test_suppressed_incident_is_not_delivered(self) -> None:
        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={RouteDestination.TELEGRAM_WITH_RCA: delivered.append},
        )
        sink(_incident(seconds=0))
        sink(_incident(seconds=10))  # within dedup window
        assert len(delivered) == 1
        snapshot = sink.metrics_snapshot()
        assert snapshot["delivered"] == 1
        assert snapshot["suppressed"] == 1

    def test_missing_route_increments_unrouted_metric(self, caplog) -> None:
        corr = IncidentCorrelator()
        sink = CorrelatingSink(correlator=corr, routes={})
        with caplog.at_level("WARNING", logger="app.hermes.correlating_sink"):
            sink(_incident())
        snapshot = sink.metrics_snapshot()
        assert snapshot["delivered"] == 0
        assert snapshot["unrouted"] == 1
        assert any("no sink registered" in r.message for r in caplog.records)

    def test_downstream_sink_exception_does_not_propagate(self) -> None:
        def boom(_: HermesIncident) -> None:
            raise RuntimeError("downstream broke")

        corr = IncidentCorrelator()
        sink = CorrelatingSink(correlator=corr, routes={RouteDestination.TELEGRAM_WITH_RCA: boom})
        # Must not raise:
        sink(_incident())
        assert sink.metrics_snapshot()["sink_errors"] == 1
        assert sink.metrics_snapshot()["delivered"] == 0

    def test_dedup_window_default_constant_is_documented(self) -> None:
        # Catches anyone tightening the default below the AlarmDispatcher cooldown.
        assert DEFAULT_DEDUP_WINDOW_S == 300.0

    def test_escalation_metric_tracks_correctly(self) -> None:
        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator(dedup_window_s=0, escalation_window_s=60, escalation_threshold=2)
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: delivered.append,
                RouteDestination.PAGER: delivered.append,
            },
        )
        sink(_incident(seconds=0))
        sink(_incident(seconds=5))  # escalates
        assert sink.metrics_snapshot()["escalated"] == 1

    def test_escalated_incident_uses_distinct_fingerprint_key(self) -> None:
        """Greptile P1: escalated incidents must reach the sink with an
        ':escalated'-suffixed fingerprint so AlarmDispatcher's cooldown
        does not suppress them under the first-occurrence bucket."""
        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator(dedup_window_s=0, escalation_window_s=60, escalation_threshold=2)
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: delivered.append,
                RouteDestination.PAGER: delivered.append,
            },
        )
        sink(_incident(seconds=0))  # first occurrence — plain fingerprint
        sink(_incident(seconds=5))  # escalates
        assert len(delivered) == 2
        fp_first = delivered[0].fingerprint
        fp_escalated = delivered[1].fingerprint
        assert not fp_first.endswith(":escalated"), "first occurrence must use plain fingerprint"
        assert fp_escalated == f"{fp_first}:escalated", (
            "escalated incident must use ':escalated'-suffixed fingerprint"
        )

    def test_close_resets_correlator_state(self) -> None:
        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={RouteDestination.TELEGRAM_WITH_RCA: delivered.append},
        )
        sink(_incident(seconds=0))
        sink(_incident(seconds=10))  # suppressed by dedup
        sink.close()
        sink(_incident(seconds=20))
        assert len(delivered) == 2

    def test_close_propagates_once_for_shared_downstream_sink(self) -> None:
        class _Closeable:
            def __init__(self) -> None:
                self.closed = 0

            def __call__(self, _incident: HermesIncident) -> None:
                return None

            def close(self) -> None:
                self.closed += 1

        closeable = _Closeable()
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM: closeable,
                RouteDestination.TELEGRAM_WITH_RCA: closeable,
            },
            default_route=closeable,
        )
        sink.close()
        assert closeable.closed == 1

    def test_close_calls_all_sinks_even_when_one_raises(self) -> None:
        """A raising close() on one downstream sink must not skip the others or
        leave _correlator.reset() uncalled (which would leak TelegramSink
        thread-pool workers)."""

        class _RaisesOnClose:
            def __call__(self, _incident: HermesIncident) -> None:
                return None

            def close(self) -> None:
                raise RuntimeError("close failed")

        class _Closeable:
            def __init__(self) -> None:
                self.closed = 0

            def __call__(self, _incident: HermesIncident) -> None:
                return None

            def close(self) -> None:
                self.closed += 1

        raiser = _RaisesOnClose()
        good = _Closeable()
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM: raiser,
                RouteDestination.TELEGRAM_WITH_RCA: good,
            },
        )
        # Seed correlator state so we can verify reset() ran.
        sink(_incident(seconds=0))

        with pytest.raises(RuntimeError, match="close failed"):
            sink.close()

        assert good.closed == 1, "good sink must be closed even when a sibling raises"

    def test_close_always_resets_correlator_even_when_sink_raises(self) -> None:
        """_correlator.reset() must run in the finally clause so dedup state is
        cleared even when a downstream close() raises."""

        class _RaisesOnClose:
            def __call__(self, _incident: HermesIncident) -> None:
                return None

            def close(self) -> None:
                raise RuntimeError("sink boom")

        delivered: list[HermesIncident] = []
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM: _RaisesOnClose(),
                RouteDestination.TELEGRAM_WITH_RCA: delivered.append,
            },
        )
        sink(_incident(seconds=0))
        sink(_incident(seconds=5))  # suppressed by dedup window

        with pytest.raises(RuntimeError):
            sink.close()

        # After close(), dedup state was reset so the next incident is not
        # suppressed.  This proves reset() ran despite the exception.
        sink(_incident(seconds=20))
        assert len(delivered) == 2, (
            "incident after close must not be deduplicated — reset() must have run"
        )
