"""Multi-channel delivery tests for :class:`CorrelatingSink`.

The correlator/sink logic itself (dedup, escalation, metrics, close
semantics) is covered by ``test_correlator.py``. This module focuses on the
*delivery* seam: a :class:`CorrelatingSink` fanning incidents out to more than
one downstream channel, with the real vendor transports mocked.

Hermes only ships a :class:`~integrations.hermes.sinks.TelegramSink` today and
its :class:`~integrations.hermes.correlator.RouteDestination` set has no SLACK
or DISCORD members. So, we exercise multi-channel fan-out the way a real
deployment would wire extra channels *now*: by registering thin, test-local
channel adapters on existing destinations and asserting each vendor transport
is (or is not) called with the right payload.

Transports mocked:

* Telegram → ``integrations.telegram.alarms.post_telegram_message``
  (reached through the production :class:`TelegramSink`/:class:`AlarmDispatcher`).
* Slack    → ``integrations.slack.delivery.send_slack_webhook_message``.
* Discord  → ``integrations.discord.delivery.post_discord_message``.

The Slack/Discord adapters are deliberately small — just enough to prove
routing reaches the vendor transport. They are test fixtures, not a proposal
for production Hermes sinks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from integrations.discord import delivery as discord_delivery
from integrations.hermes.correlating_sink import CorrelatingSink
from integrations.hermes.correlator import IncidentCorrelator, RouteDestination
from integrations.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from integrations.hermes.sinks import TelegramSink
from integrations.slack import delivery as slack_delivery
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials

_TS = datetime(2026, 5, 12, 12, 0, 0)


# ---------------------------------------------------------------------------
# Fixtures / builders


def _record(seconds: int = 0) -> LogRecord:
    ts = _TS + timedelta(seconds=seconds)
    return LogRecord(
        timestamp=ts,
        level=LogLevel.ERROR,
        logger="hermes.agent",
        message="boom",
        raw=f"{ts.isoformat()} ERROR hermes.agent: boom",
    )


def _incident(
    *,
    rule: str = "error_severity",
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    fingerprint: str = "fp-1",
    title: str = "ERROR from hermes.agent",
    seconds: int = 0,
) -> HermesIncident:
    return HermesIncident(
        rule=rule,
        severity=severity,
        title=title,
        detected_at=_TS + timedelta(seconds=seconds),
        logger="hermes.agent",
        fingerprint=fingerprint,
        records=(_record(seconds=seconds),),
    )


class _Channels:
    """Captured calls for each mocked vendor transport."""

    def __init__(self) -> None:
        self.telegram: list[dict[str, Any]] = []
        self.slack: list[dict[str, Any]] = []
        self.discord: list[dict[str, Any]] = []

    @property
    def counts(self) -> tuple[int, int, int]:
        return len(self.telegram), len(self.slack), len(self.discord)


def _mock_channels(monkeypatch: pytest.MonkeyPatch) -> _Channels:
    """Patch all three vendor transports and record their calls."""
    channels = _Channels()

    def _fake_telegram(
        chat_id: str,
        text: str,
        bot_token: str,
        parse_mode: str = "",
        reply_to_message_id: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        channels.telegram.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    def _fake_slack(text: str, **kwargs: Any) -> tuple[bool, str]:
        channels.slack.append({"text": text, "kwargs": kwargs})
        return True, ""

    def _fake_discord(
        channel_id: str,
        embeds: list[dict[str, Any]],
        bot_token: str,
        content: str = "",
    ) -> tuple[bool, str, str]:
        channels.discord.append({"channel_id": channel_id, "content": content, "embeds": embeds})
        return True, "", "42"

    monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _fake_telegram)
    monkeypatch.setattr(slack_delivery, "send_slack_webhook_message", _fake_slack)
    monkeypatch.setattr(discord_delivery, "post_discord_message", _fake_discord)
    return channels


def _telegram_sink() -> TelegramSink:
    creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
    # cooldown 0 so dedup is driven purely by the correlator, not the dispatcher.
    return TelegramSink(AlarmDispatcher(creds, cooldown_seconds=0.0))


def _slack_sink() -> Any:
    """A minimal Slack channel adapter: format + post via the vendor transport.

    Resolved at call time (module attribute access) so ``monkeypatch.setattr``
    on ``slack_delivery.send_slack_webhook_message`` is honoured.
    """

    def _sink(incident: HermesIncident) -> None:
        text = f"[{incident.severity.value.upper()}] {incident.title} ({incident.rule})"
        slack_delivery.send_slack_webhook_message(text, webhook_url="https://hooks.example/x")

    return _sink


def _discord_sink() -> Any:
    def _sink(incident: HermesIncident) -> None:
        content = f"[{incident.severity.value.upper()}] {incident.title} ({incident.rule})"
        discord_delivery.post_discord_message(
            channel_id="chan-1", embeds=[], bot_token="bot-tok", content=content
        )

    return _sink


def _three_channel_sink(
    monkeypatch: pytest.MonkeyPatch,
    *,
    correlator: IncidentCorrelator | None = None,
) -> tuple[CorrelatingSink, _Channels]:
    channels = _mock_channels(monkeypatch)
    corr = correlator if correlator is not None else IncidentCorrelator()
    sink = CorrelatingSink(
        correlator=corr,
        routes={
            RouteDestination.TELEGRAM_WITH_RCA: _telegram_sink(),
            RouteDestination.TELEGRAM: _slack_sink(),
            RouteDestination.PAGER: _discord_sink(),
        },
    )
    return sink, channels


# ---------------------------------------------------------------------------
# Fan-out routing


class TestChannelFanOut:
    def test_rca_route_hits_only_telegram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink, channels = _three_channel_sink(monkeypatch)

        # error_severity → TELEGRAM_WITH_RCA in the default matrix.
        sink(_incident(rule="error_severity", severity=IncidentSeverity.HIGH))

        assert channels.counts == (1, 0, 0)
        assert "Hermes incident" in channels.telegram[0]["text"]

    def test_warning_burst_route_hits_only_slack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink, channels = _three_channel_sink(monkeypatch)

        # warning_burst → TELEGRAM (wired here to the Slack adapter).
        sink(
            _incident(
                rule="warning_burst",
                severity=IncidentSeverity.MEDIUM,
                fingerprint="fp-warn",
            )
        )

        assert channels.counts == (0, 1, 0)
        assert "warning_burst" in channels.slack[0]["text"]
        assert channels.slack[0]["kwargs"]["webhook_url"] == "https://hooks.example/x"

    def test_pager_route_hits_only_discord(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink, channels = _three_channel_sink(monkeypatch)

        # crash_loop → PAGER (wired here to the Discord adapter).
        sink(
            _incident(
                rule="crash_loop",
                severity=IncidentSeverity.HIGH,
                fingerprint="fp-crash",
            )
        )

        assert channels.counts == (0, 0, 1)
        assert "crash_loop" in channels.discord[0]["content"]

    def test_distinct_incidents_fan_out_to_distinct_channels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mixed batch must land each incident on exactly its routed channel
        and leave the others untouched."""
        sink, channels = _three_channel_sink(monkeypatch)

        sink(_incident(rule="error_severity", severity=IncidentSeverity.HIGH, fingerprint="a"))
        sink(_incident(rule="warning_burst", severity=IncidentSeverity.MEDIUM, fingerprint="b"))
        sink(_incident(rule="crash_loop", severity=IncidentSeverity.HIGH, fingerprint="c"))

        assert channels.counts == (1, 1, 1)
        assert sink.metrics_snapshot()["delivered"] == 3


class TestNonDeliveringDecisions:
    def test_dropped_incident_touches_no_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink, channels = _three_channel_sink(monkeypatch)

        # Unknown rule + MEDIUM severity → DROP in the fallback route.
        sink(_incident(rule="unknown_rule", severity=IncidentSeverity.MEDIUM))

        assert channels.counts == (0, 0, 0)
        assert sink.metrics_snapshot()["dropped"] == 1

    def test_suppressed_duplicate_touches_no_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sink, channels = _three_channel_sink(monkeypatch)

        sink(_incident(fingerprint="dup", seconds=0))
        sink(_incident(fingerprint="dup", seconds=10))  # within dedup window

        assert channels.counts == (1, 0, 0)
        snap = sink.metrics_snapshot()
        assert snap["delivered"] == 1
        assert snap["suppressed"] == 1

    def test_unrouted_destination_touches_no_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A PAGER-routed incident with no PAGER handler must be counted
        unrouted and reach none of the wired channels."""
        channels = _mock_channels(monkeypatch)
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: _telegram_sink(),
                RouteDestination.TELEGRAM: _slack_sink(),
                # PAGER intentionally omitted.
            },
        )

        sink(_incident(rule="crash_loop", severity=IncidentSeverity.HIGH, fingerprint="fp-x"))

        assert channels.counts == (0, 0, 0)
        assert sink.metrics_snapshot()["unrouted"] == 1

    def test_default_route_catches_unregistered_destination(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        channels = _mock_channels(monkeypatch)
        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={RouteDestination.TELEGRAM_WITH_RCA: _telegram_sink()},
            default_route=_discord_sink(),
        )

        # crash_loop → PAGER, unregistered, so the default (Discord) catches it.
        sink(_incident(rule="crash_loop", severity=IncidentSeverity.HIGH, fingerprint="fp-d"))

        assert channels.counts == (0, 0, 1)
        assert sink.metrics_snapshot()["delivered"] == 1


class TestChannelIsolation:
    def test_one_channel_raising_does_not_starve_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A downstream channel that raises must be isolated: its incident is
        counted as a sink error, but incidents routed to healthy channels keep
        flowing."""
        channels = _mock_channels(monkeypatch)

        def _boom_slack(incident: HermesIncident) -> None:
            raise RuntimeError("slack webhook exploded")

        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: _telegram_sink(),
                RouteDestination.TELEGRAM: _boom_slack,
            },
        )

        # Route to the broken Slack channel first…
        sink(_incident(rule="warning_burst", severity=IncidentSeverity.MEDIUM, fingerprint="w"))
        # …then to the healthy Telegram channel.
        sink(_incident(rule="error_severity", severity=IncidentSeverity.HIGH, fingerprint="e"))

        assert channels.counts == (1, 0, 0)  # telegram delivered, slack never captured
        snap = sink.metrics_snapshot()
        assert snap["sink_errors"] == 1
        assert snap["delivered"] == 1

    def test_escalated_fingerprint_reaches_downstream_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Escalation must break through dedup AND carry the ':escalated'
        fingerprint all the way to the Telegram transport so a downstream
        AlarmDispatcher cooldown cannot re-suppress it. We assert two distinct
        deliveries reached the wire for the same underlying event."""
        corr = IncidentCorrelator(dedup_window_s=0, escalation_window_s=60, escalation_threshold=2)
        channels = _mock_channels(monkeypatch)
        # A dedicated dispatcher with a real cooldown; distinct fingerprints
        # are what let the second (escalated) send through.
        creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
        telegram = TelegramSink(AlarmDispatcher(creds, cooldown_seconds=300.0))
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: telegram,
                RouteDestination.PAGER: telegram,
            },
        )

        sink(_incident(fingerprint="esc", seconds=0))  # first, plain fingerprint
        sink(_incident(fingerprint="esc", seconds=5))  # escalates → CRITICAL → PAGER

        # Both reached the transport: the cooldown did not swallow the second
        # because its fingerprint was suffixed with ':escalated'.
        assert len(channels.telegram) == 2


class TestCloseFanOut:
    def test_close_closes_every_channel_and_resets_dedup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        channels = _mock_channels(monkeypatch)

        closed: list[str] = []

        class _ClosableSlack:
            def __call__(self, incident: HermesIncident) -> None:
                slack_delivery.send_slack_webhook_message(incident.title)

            def close(self) -> None:
                closed.append("slack")

        corr = IncidentCorrelator()
        sink = CorrelatingSink(
            correlator=corr,
            routes={
                RouteDestination.TELEGRAM_WITH_RCA: _telegram_sink(),
                RouteDestination.TELEGRAM: _ClosableSlack(),
            },
        )

        sink(_incident(fingerprint="c", seconds=0))
        sink(_incident(fingerprint="c", seconds=10))  # suppressed by dedup
        assert channels.counts == (1, 0, 0)

        sink.close()
        assert closed == ["slack"]

        # After close(), correlator dedup state is cleared, so the same
        # fingerprint delivers again instead of being suppressed.
        sink(_incident(fingerprint="c", seconds=20))
        assert channels.counts == (2, 0, 0)
