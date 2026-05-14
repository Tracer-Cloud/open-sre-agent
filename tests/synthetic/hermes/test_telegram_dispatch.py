"""Synthetic end-to-end test: HermesAgent → TelegramSink → AlarmDispatcher.

Distinct from ``test_suite.py``, which only exercises the classifier.
This test feeds a realistic ``errors.log`` slice through the full
incident pipeline and asserts that the Telegram dispatcher receives a
correctly-formatted message for each detected incident, with
fingerprint-keyed dedup applied across repeats.

The log fixture is synthesized inline rather than read from disk so the
test does not depend on the per-scenario ``errors.log`` files (those
are ``.gitignore``d).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.hermes.agent import HermesAgent
from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident
from app.hermes.sinks import TelegramSink
from app.watch_dog.alarms import AlarmCredentials, AlarmDispatcher

pytestmark = pytest.mark.synthetic


# 9 WARNING lines from the Telegram polling-conflict scenario (~22.5s
# cadence), enough to trigger 3 warning bursts with a threshold of 3.
_POLLING_CONFLICT_LOG = [
    "2026-05-12 00:40:12,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (1/3), will retry in 10s.",
    "2026-05-12 00:40:34,500 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (2/3), will retry in 10s.",
    "2026-05-12 00:40:57,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (3/3), will retry in 10s.",
    "2026-05-12 00:41:19,500 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (1/3), will retry in 10s.",
    "2026-05-12 00:41:42,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (2/3), will retry in 10s.",
    "2026-05-12 00:42:04,500 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (3/3), will retry in 10s.",
    "2026-05-12 00:42:27,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (1/3), will retry in 10s.",
    "2026-05-12 00:42:49,500 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (2/3), will retry in 10s.",
    "2026-05-12 00:43:12,000 WARNING gateway.platforms.telegram: "
    + "[Telegram] Telegram polling conflict (3/3), will retry in 10s.",
]


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(chat_id: str, text: str, bot_token: str) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    monkeypatch.setattr("app.watch_dog.alarms.post_telegram_message", _fake_post)
    return calls


def test_polling_conflict_dispatches_warning_burst_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three bursts of 3 warnings each should produce one Telegram dispatch.

    All bursts share the same ``warning_burst`` fingerprint (rule +
    logger + empty message), so the dispatcher's cooldown collapses
    them into a single send — exactly the behaviour the operator wants
    when a single subsystem floods.
    """
    calls = _patch_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok", chat_id="chat-1")
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
    sink = TelegramSink(dispatcher)

    incidents: list[HermesIncident] = []

    def _tee(incident: HermesIncident) -> None:
        incidents.append(incident)
        sink(incident)

    agent = HermesAgent(
        sink=_tee,
        log_path="/dev/null",
        classifier=IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=60.0),
    )

    agent.process(_POLLING_CONFLICT_LOG)

    # Classifier emits 3 bursts (9 lines / threshold of 3, bucket
    # drains on emit). All share the same fingerprint.
    assert len(incidents) == 3
    fingerprints = {incident.fingerprint for incident in incidents}
    assert len(fingerprints) == 1, "warning_burst fingerprint must be stable across bursts"

    # Dispatcher cooldown suppresses the second and third dispatch.
    assert len(calls) == 1
    text = calls[0]["text"]
    assert "Hermes incident" in text
    assert "warning_burst" in text
    assert "gateway.platforms.telegram" in text
    # Notify-only marker present (MEDIUM severity = no investigation).
    assert "notify only" in text


def test_distinct_fingerprints_all_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different incident fingerprints bypass cooldown, so each gets sent."""
    calls = _patch_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok", chat_id="chat-1")
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=300.0)
    sink = TelegramSink(dispatcher)

    agent = HermesAgent(
        sink=sink,
        log_path="/dev/null",
        classifier=IncidentClassifier(warning_burst_threshold=2, warning_burst_window_s=60.0),
    )

    # Two ERROR records from distinct loggers/messages → two
    # error_severity incidents with different fingerprints.
    agent.process(
        [
            "2026-05-12 00:00:00,000 ERROR backend.api: database connection refused",
            "2026-05-12 00:00:01,000 ERROR worker.queue: failed to ack message",
        ]
    )

    assert len(calls) == 2
