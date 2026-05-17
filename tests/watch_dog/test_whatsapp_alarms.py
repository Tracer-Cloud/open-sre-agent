"""Tests for app/watch_dog/whatsapp_alarms.py."""

from __future__ import annotations

from typing import Any

import pytest

from app.cli.support.errors import OpenSREError
from app.watch_dog.whatsapp_alarms import (
    WhatsAppAlarmCredentials,
    WhatsAppAlarmDispatcher,
    load_whatsapp_credentials_from_env,
)


def _stub_whatsapp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ok: bool = True,
    error: str = "",
    captured: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = captured if captured is not None else []

    def _fake_post(
        to: str,
        text: str,
        phone_number_id: str,
        access_token: str,
    ) -> tuple[bool, str, str]:
        calls.append(
            {
                "to": to,
                "text": text,
                "phone_number_id": phone_number_id,
                "access_token": access_token,
            }
        )
        return ok, error, "wamid.123" if ok else ""

    monkeypatch.setattr(
        "app.watch_dog.whatsapp_alarms.post_whatsapp_message",
        _fake_post,
    )
    return calls


def _patch_clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    iterator = iter(ticks)

    def _now() -> float:
        return next(iterator)

    monkeypatch.setattr(WhatsAppAlarmDispatcher, "_now", staticmethod(_now))


def test_whatsapp_alarm_credentials_repr_does_not_leak_access_token() -> None:
    creds = WhatsAppAlarmCredentials(
        access_token="super-secret-token",
        phone_number_id="12345",
        to="+1234567890",
    )

    rendered = repr(creds)

    assert "super-secret-token" not in rendered
    assert "+1234567890" in rendered


def test_load_credentials_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok-123")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid-1")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+1234567890")

    creds = load_whatsapp_credentials_from_env()

    assert creds == WhatsAppAlarmCredentials(
        access_token="tok-123", phone_number_id="pnid-1", to="+1234567890"
    )


def test_load_credentials_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "  tok-123  ")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "\tpnid-1\n")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "  +1234567890  ")

    creds = load_whatsapp_credentials_from_env()

    assert creds.access_token == "tok-123"
    assert creds.phone_number_id == "pnid-1"
    assert creds.to == "+1234567890"


def test_load_credentials_to_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "from-env")

    creds = load_whatsapp_credentials_from_env(to_override="from-arg")

    assert creds.to == "from-arg"


def test_load_credentials_missing_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+123")

    with pytest.raises(OpenSREError) as exc_info:
        load_whatsapp_credentials_from_env()

    assert "WHATSAPP_ACCESS_TOKEN" in str(exc_info.value)


def test_load_credentials_blank_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "   ")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+123")

    with pytest.raises(OpenSREError):
        load_whatsapp_credentials_from_env()


def test_load_credentials_missing_phone_number_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+123")

    with pytest.raises(OpenSREError) as exc_info:
        load_whatsapp_credentials_from_env()

    assert "WHATSAPP_PHONE_NUMBER_ID" in str(exc_info.value)


def test_load_credentials_missing_to(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid")
    monkeypatch.delenv("WHATSAPP_DEFAULT_TO", raising=False)

    with pytest.raises(OpenSREError) as exc_info:
        load_whatsapp_credentials_from_env()

    assert "recipient" in str(exc_info.value).lower()


def test_first_dispatch_calls_whatsapp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )

    assert dispatcher.dispatch("max_cpu", "CPU pegged at 95%") is True
    assert len(calls) == 1
    assert calls[0] == {
        "to": "+123",
        "text": "CPU pegged at 95%",
        "phone_number_id": "pnid",
        "access_token": "tok",
    }


def test_second_dispatch_within_cooldown_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 200.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 1
    assert calls[0]["text"] == "first"


def test_second_dispatch_after_cooldown_calls_whatsapp_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 450.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is True
    assert len(calls) == 2
    assert calls[1]["text"] == "second"


def test_cooldown_is_per_threshold_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 110.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "cpu") is True
    assert dispatcher.dispatch("max_runtime", "runtime") is True
    assert len(calls) == 2


def test_dispatch_returns_false_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _stub_whatsapp(monkeypatch, ok=False, error="network down", captured=calls)
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 2


def test_dispatch_uses_credentials_from_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok-XYZ", phone_number_id="pnid-ABC", to="+999"),
    )
    dispatcher.dispatch("max_runtime", "process exceeded 5m")

    assert calls[0]["access_token"] == "tok-XYZ"
    assert calls[0]["phone_number_id"] == "pnid-ABC"
    assert calls[0]["to"] == "+999"


def test_dispatch_truncates_messages_over_whatsapp_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_whatsapp(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = WhatsAppAlarmDispatcher(
        WhatsAppAlarmCredentials(access_token="tok", phone_number_id="pnid", to="+123"),
    )
    oversized = "X" * 5000
    assert dispatcher.dispatch("max_cpu", oversized) is True
    assert len(calls[0]["text"]) <= 4096
    assert calls[0]["text"].endswith("…")
