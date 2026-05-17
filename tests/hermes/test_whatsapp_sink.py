"""Tests for app/hermes/whatsapp_sink.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.hermes.incident import HermesIncident, IncidentSeverity
from app.hermes.whatsapp_sink import (
    WhatsAppSink,
    WhatsAppSinkConfig,
    make_whatsapp_sink,
)
from app.watch_dog.whatsapp_alarms import WhatsAppAlarmCredentials, WhatsAppAlarmDispatcher

pytestmark = pytest.mark.synthetic


def _fake_incident(
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    title: str = "Test incident",
) -> HermesIncident:
    return HermesIncident(
        severity=severity,
        title=title,
        rule="test-rule",
        logger="test.logger",
        detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        fingerprint="fp-1",
        records=(),
    )


def _patch_whatsapp(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(
        to: str,
        text: str,
        phone_number_id: str,
        access_token: str,
    ) -> tuple[bool, str, str]:
        calls.append({"to": to, "text": text, "phone_number_id": phone_number_id})
        return True, "", "wamid.1"

    monkeypatch.setattr(
        "app.watch_dog.whatsapp_alarms.post_whatsapp_message",
        _fake_post,
    )
    return calls


def test_whatsapp_sink_formats_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_whatsapp(monkeypatch)
    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = WhatsAppSink(dispatcher)

    incident = _fake_incident(severity=IncidentSeverity.CRITICAL, title="DB timeout")
    sink(incident)

    assert len(calls) == 1
    assert "DB timeout" in calls[0]["text"]
    assert "CRITICAL" in calls[0]["text"]
    assert calls[0]["to"] == "+123"
    assert calls[0]["phone_number_id"] == "pnid"


def test_whatsapp_sink_dedup_by_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_whatsapp(monkeypatch)
    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
        cooldown_seconds=300.0,
    )
    sink = WhatsAppSink(dispatcher)

    incident = _fake_incident()
    sink(incident)
    sink(incident)  # same fingerprint within cooldown

    assert len(calls) == 1


def test_whatsapp_sink_with_investigation_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_whatsapp(monkeypatch)

    def _bridge(incident: HermesIncident) -> str | None:
        return "Root cause: connection pool exhausted"

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = WhatsAppSink(dispatcher, investigation_bridge=_bridge)

    incident = _fake_incident(severity=IncidentSeverity.HIGH)
    sink(incident)

    assert len(calls) == 1
    assert "Root cause: connection pool exhausted" in calls[0]["text"]


def test_whatsapp_sink_bridge_run_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_whatsapp(monkeypatch)

    def _bridge(incident: HermesIncident) -> str | None:
        return "Inline result"

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = WhatsAppSink(
        dispatcher,
        investigation_bridge=_bridge,
        config=WhatsAppSinkConfig(bridge_run_inline=True),
    )

    incident = _fake_incident(severity=IncidentSeverity.HIGH)
    sink(incident)

    assert len(calls) == 1
    assert "Inline result" in calls[0]["text"]


def test_whatsapp_sink_medium_severity_skips_investigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_whatsapp(monkeypatch)

    def _bridge(incident: HermesIncident) -> str | None:
        return "Should not appear"

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = WhatsAppSink(dispatcher, investigation_bridge=_bridge)

    incident = _fake_incident(severity=IncidentSeverity.MEDIUM)
    sink(incident)

    assert len(calls) == 1
    assert "Should not appear" not in calls[0]["text"]


def test_whatsapp_sink_close_is_safe_to_call_multiple_times() -> None:
    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = WhatsAppSink(dispatcher)

    sink.close()
    sink.close()  # should not raise


def test_make_whatsapp_sink_returns_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_whatsapp(monkeypatch)
    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    sink = make_whatsapp_sink(dispatcher)

    incident = _fake_incident()
    sink(incident)

    assert len(calls) == 1
