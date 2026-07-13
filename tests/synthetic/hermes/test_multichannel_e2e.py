"""Synthetic end-to-end test: HermesAgent → CorrelatingSink → 3 channels.

Complements ``test_telegram_dispatch.py`` (single-channel Telegram) by driving
a realistic ``errors.log`` slice through the *full* pipeline into a
:class:`CorrelatingSink` that fans out to three delivery channels — Telegram,
Slack, and Discord — with every vendor transport mocked.

Currently Hermes only routes to Telegram in production today, so Slack/Discord are wired here through thin test-local
channel adapters registered on existing :class:`RouteDestination` values. The
point of the test is the *routing + dedup + escalation* behaviour that a
multi-channel deployment depends on, observed at the transport boundary.

Rule → destination (default matrix) → channel wired in this test:

* ``error_severity``/``traceback`` → ``TELEGRAM_WITH_RCA`` → Telegram
* ``warning_burst``               → ``TELEGRAM``          → Slack adapter
* ``disk_full``                   → ``PAGER``             → Discord adapter

The log fixture is synthesized inline so the test does not depend on the
per-scenario ``errors.log`` files (which are ``.gitignore``d).
"""

from __future__ import annotations

from typing import Any

import pytest

from integrations.discord import delivery as discord_delivery
from integrations.hermes.agent import HermesAgent
from integrations.hermes.classifier import IncidentClassifier
from integrations.hermes.correlating_sink import CorrelatingSink
from integrations.hermes.correlator import IncidentCorrelator, RouteDestination
from integrations.hermes.incident import HermesIncident
from integrations.hermes.sinks import TelegramSink
from integrations.slack import delivery as slack_delivery
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials

pytestmark = pytest.mark.synthetic


class _Channels:
    def __init__(self) -> None:
        self.telegram: list[str] = []
        self.slack: list[str] = []
        self.discord: list[str] = []

    @property
    def counts(self) -> tuple[int, int, int]:
        return len(self.telegram), len(self.slack), len(self.discord)


def _mock_channels(monkeypatch: pytest.MonkeyPatch) -> _Channels:
    channels = _Channels()

    def _fake_telegram(
        chat_id: str,
        text: str,
        bot_token: str,
        parse_mode: str = "",
        reply_to_message_id: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        channels.telegram.append(text)
        return True, "", "1"

    def _fake_slack(text: str, **_kw: Any) -> tuple[bool, str]:
        channels.slack.append(text)
        return True, ""

    def _fake_discord(
        channel_id: str,
        embeds: list[dict[str, Any]],
        bot_token: str,
        content: str = "",
    ) -> tuple[bool, str, str]:
        channels.discord.append(content)
        return True, "", "42"

    monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _fake_telegram)
    monkeypatch.setattr(slack_delivery, "send_slack_webhook_message", _fake_slack)
    monkeypatch.setattr(discord_delivery, "post_discord_message", _fake_discord)
    return channels


def _slack_adapter(incident: HermesIncident) -> None:
    text = f"[{incident.severity.value.upper()}] {incident.title} ({incident.rule})"
    slack_delivery.send_slack_webhook_message(text, webhook_url="https://hooks.example/x")


def _discord_adapter(incident: HermesIncident) -> None:
    content = f"[{incident.severity.value.upper()}] {incident.title} ({incident.rule})"
    discord_delivery.post_discord_message(
        channel_id="chan-1", embeds=[], bot_token="bot-tok", content=content
    )


def _build_pipeline(
    *,
    correlator: IncidentCorrelator,
    telegram_cooldown_s: float = 0.0,
) -> tuple[HermesAgent, CorrelatingSink]:
    creds = TelegramCredentials(bot_token="tok", chat_id="chat-1")
    telegram = TelegramSink(AlarmDispatcher(creds, cooldown_seconds=telegram_cooldown_s))
    sink = CorrelatingSink(
        correlator=correlator,
        routes={
            RouteDestination.TELEGRAM_WITH_RCA: telegram,
            RouteDestination.TELEGRAM: _slack_adapter,
            RouteDestination.PAGER: _discord_adapter,
        },
    )
    agent = HermesAgent(
        sink=sink,
        log_path="/dev/null",
        classifier=IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=120.0),
    )
    return agent, sink


# A mixed log slice covering all three channels:
#   - 3 WARNING polling-conflict lines (same logger) → one warning_burst → Slack
#   - 2 ERROR lines (distinct loggers)               → two error_severity → Telegram
#   - 1 WARNING "no space left on device" line       → one disk_full     → Discord
_MIXED_LOG = [
    "2026-05-12 00:40:12,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (1/3), will retry in 10s.",
    "2026-05-12 00:40:34,500 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (2/3), will retry in 10s.",
    "2026-05-12 00:40:57,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (3/3), will retry in 10s.",
    "2026-05-12 00:41:05,000 ERROR backend.api: database connection refused",
    "2026-05-12 00:41:06,000 ERROR worker.queue: failed to ack message",
    "2026-05-12 00:41:10,000 WARNING storage.writer: no space left on device",
]


class TestMultiChannelFanOut:
    def test_mixed_log_routes_each_incident_to_its_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        channels = _mock_channels(monkeypatch)
        agent, sink = _build_pipeline(correlator=IncidentCorrelator())

        agent.process(_MIXED_LOG)

        # Telegram: two distinct error_severity incidents.
        # Slack: one warning_burst. Discord: one disk_full.
        assert channels.counts == (2, 1, 1), (
            f"unexpected fan-out: telegram/slack/discord = {channels.counts}"
        )
        assert all("Hermes incident" in t for t in channels.telegram)
        assert "warning_burst" in channels.slack[0]
        assert "disk_full" in channels.discord[0]
        assert sink.metrics_snapshot()["delivered"] == 4

    def test_no_channel_receives_dropped_incidents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A benign INFO/low-severity line produces no incident and therefore
        touches no channel — the pipeline must stay silent."""
        channels = _mock_channels(monkeypatch)
        agent, _ = _build_pipeline(correlator=IncidentCorrelator())

        agent.process(
            [
                "2026-05-12 00:00:00,000 INFO backend.api: request served in 12ms",
                "2026-05-12 00:00:01,000 DEBUG backend.api: cache hit",
            ]
        )

        assert channels.counts == (0, 0, 0)


class TestMultiChannelDedup:
    def test_repeated_error_is_deduped_before_reaching_telegram(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two identical ERROR lines inside the correlator dedup window collapse
        to a single Telegram delivery; Slack/Discord stay untouched."""
        channels = _mock_channels(monkeypatch)
        # Default 300s dedup window; both lines are 30s apart.
        agent, sink = _build_pipeline(correlator=IncidentCorrelator())

        agent.process(
            [
                "2026-05-12 00:00:00,000 ERROR backend.api: database connection refused",
                "2026-05-12 00:00:30,000 ERROR backend.api: database connection refused",
            ]
        )

        assert channels.counts == (1, 0, 0)
        snap = sink.metrics_snapshot()
        assert snap["delivered"] == 1
        assert snap["suppressed"] == 1

    def test_telegram_cooldown_suppresses_after_correlator_lets_both_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline has two independent suppression layers. With correlator
        dedup disabled, both incidents are *delivered* to the Telegram sink, but
        the AlarmDispatcher's per-fingerprint cooldown collapses them into a
        single wire call — proving the second layer works on its own."""
        channels = _mock_channels(monkeypatch)
        agent, sink = _build_pipeline(
            correlator=IncidentCorrelator(dedup_window_s=0.0),
            telegram_cooldown_s=300.0,
        )

        agent.process(
            [
                "2026-05-12 00:00:00,000 ERROR backend.api: database connection refused",
                "2026-05-12 00:00:30,000 ERROR backend.api: database connection refused",
            ]
        )

        # Correlator suppressed nothing: both reached the sink.
        snap = sink.metrics_snapshot()
        assert snap["delivered"] == 2
        assert snap["suppressed"] == 0
        # …but the dispatcher cooldown let only the first one onto the wire.
        assert channels.counts == (1, 0, 0)


class TestMultiChannelEscalation:
    def test_repeated_warning_burst_escalates_and_switches_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``warning_burst`` (MEDIUM → Slack) that repeats past the escalation
        threshold is bumped to HIGH and, because the burst fingerprint is stable,
        breaks through dedup. The escalated incident re-routes: MEDIUM→HIGH still
        maps ``warning_burst`` to TELEGRAM (Slack here), so both land on Slack but
        the second is an escalation, proving escalation crosses the sink boundary."""
        channels = _mock_channels(monkeypatch)
        corr = IncidentCorrelator(
            dedup_window_s=300.0, escalation_window_s=600.0, escalation_threshold=2
        )
        agent, sink = _build_pipeline(correlator=corr)

        # Two full bursts (3 warnings each) from the same logger → two
        # warning_burst incidents sharing one fingerprint. The second repeat
        # escalates and breaks through the dedup window.
        burst = [
            "2026-05-12 00:40:12,000 WARNING gateway.platforms.telegram: polling conflict (1/3)",
            "2026-05-12 00:40:34,500 WARNING gateway.platforms.telegram: polling conflict (2/3)",
            "2026-05-12 00:40:57,000 WARNING gateway.platforms.telegram: polling conflict (3/3)",
            "2026-05-12 00:41:12,000 WARNING gateway.platforms.telegram: polling conflict (1/3)",
            "2026-05-12 00:41:34,500 WARNING gateway.platforms.telegram: polling conflict (2/3)",
            "2026-05-12 00:41:57,000 WARNING gateway.platforms.telegram: polling conflict (3/3)",
        ]
        agent.process(burst)

        # First burst delivered; second escalated through dedup → 2 Slack sends.
        assert channels.counts == (0, 2, 0)
        snap = sink.metrics_snapshot()
        assert snap["delivered"] == 2
        assert snap["escalated"] == 1
        # The escalated delivery carries the ESCALATED marker in its title.
        assert any("ESCALATED" in t for t in channels.slack)
