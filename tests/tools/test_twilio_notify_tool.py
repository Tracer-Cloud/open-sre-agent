"""Tests for tools/TwilioNotifyTool — SMS notification surface."""

from __future__ import annotations

from typing import Any

import pytest

from tools.TwilioNotifyTool import TwilioNotifyTool, twilio_notify


@pytest.fixture
def twilio_source() -> dict[str, Any]:
    """The flat/runtime ``sources`` shape passed to is_available()."""
    return {
        "twilio": {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {
                "enabled": True,
                "from_number": "+14155551111",
                "messaging_service_sid": "",
                "default_to": "+14155550000",
            },
        }
    }


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, twilio_config: dict[str, Any] | None) -> None:
    """Patch resolve_effective_integrations to return a wrapped twilio entry."""
    effective: dict[str, Any] = {}
    if twilio_config is not None:
        effective["twilio"] = {"config": twilio_config, "source": "local env"}
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: effective,
    )


def test_metadata_declares_twilio_source() -> None:
    metadata = TwilioNotifyTool.metadata()
    assert metadata.name == "twilio_notify"
    assert metadata.source == "twilio"


# ---- is_available -------------------------------------------------------------


def test_is_available_true_when_sms_configured(twilio_source: dict[str, Any]) -> None:
    assert twilio_notify.is_available(twilio_source) is True


def test_is_available_false_when_no_twilio() -> None:
    assert twilio_notify.is_available({}) is False


def test_is_available_false_when_sms_disabled(twilio_source: dict[str, Any]) -> None:
    twilio_source["twilio"]["sms"]["enabled"] = False
    assert twilio_notify.is_available(twilio_source) is False


def test_is_available_false_when_no_sender(twilio_source: dict[str, Any]) -> None:
    twilio_source["twilio"]["sms"]["from_number"] = ""
    twilio_source["twilio"]["sms"]["messaging_service_sid"] = ""
    assert twilio_notify.is_available(twilio_source) is False


def test_is_available_true_with_only_messaging_service(twilio_source: dict[str, Any]) -> None:
    twilio_source["twilio"]["sms"]["from_number"] = ""
    twilio_source["twilio"]["sms"]["messaging_service_sid"] = "MG1"
    assert twilio_notify.is_available(twilio_source) is True


# ---- credentials never travel through traced kwargs --------------------------


def test_extract_params_returns_no_credentials(twilio_source: dict[str, Any]) -> None:
    """extract_params output is serialized into traces — it must hold no secrets."""
    params = twilio_notify.extract_params(twilio_source)
    assert params == {}


# ---- run ----------------------------------------------------------------------


def test_run_resolves_credentials_internally_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolve(
        monkeypatch,
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {
                "enabled": True,
                "from_number": "+14155551111",
                "messaging_service_sid": "",
                "default_to": "+14155550000",
            },
        },
    )
    captured: dict[str, Any] = {}

    def _fake_send(report: str, ctx: dict[str, Any]) -> tuple[bool, str, str]:
        captured["report"] = report
        captured["ctx"] = ctx
        return True, "", "SM-SENT"

    monkeypatch.setattr("tools.TwilioNotifyTool.send_twilio_sms_report", _fake_send)

    result = twilio_notify.run(body="page on-call", to="+14155559999")

    assert result["status"] == "sent"
    assert result["sid"] == "SM-SENT"
    assert captured["report"] == "page on-call"
    assert captured["ctx"]["to"] == "+14155559999"
    assert captured["ctx"]["account_sid"] == "AC1"
    assert captured["ctx"]["auth_token"] == "tok"


def test_run_falls_back_to_default_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(
        monkeypatch,
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {
                "enabled": True,
                "from_number": "+14155551111",
                "messaging_service_sid": "",
                "default_to": "+14155550000",
            },
        },
    )
    captured: dict[str, Any] = {}

    def _fake_send(report: str, ctx: dict[str, Any]) -> tuple[bool, str, str]:
        captured["ctx"] = ctx
        return True, "", "SM-DEF"

    monkeypatch.setattr("tools.TwilioNotifyTool.send_twilio_sms_report", _fake_send)

    result = twilio_notify.run(body="hi")

    assert result["status"] == "sent"
    assert captured["ctx"]["to"] == "+14155550000"


def test_run_failed_when_twilio_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, None)

    result = twilio_notify.run(body="hi", to="+14155550000")

    assert result["status"] == "failed"
    assert "not configured" in result["error"].lower()
    assert result["sid"] == ""


def test_run_failed_when_no_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(
        monkeypatch,
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {
                "enabled": True,
                "from_number": "+14155551111",
                "messaging_service_sid": "",
                "default_to": "",
            },
        },
    )

    result = twilio_notify.run(body="hi")

    assert result["status"] == "failed"
    assert "recipient" in result["error"].lower()


def test_run_propagates_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(
        monkeypatch,
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {
                "enabled": True,
                "from_number": "+14155551111",
                "messaging_service_sid": "",
                "default_to": "+14155550000",
            },
        },
    )
    monkeypatch.setattr(
        "tools.TwilioNotifyTool.send_twilio_sms_report",
        lambda _r, _c: (False, "twilio rejected", ""),
    )

    result = twilio_notify.run(body="hi", to="+14155550000")

    assert result["status"] == "failed"
    assert result["error"] == "twilio rejected"
    assert result["sid"] == ""
