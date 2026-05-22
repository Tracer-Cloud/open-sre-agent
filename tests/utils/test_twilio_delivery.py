"""Tests for app/utils/twilio_delivery.py — Twilio SMS transport."""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.twilio_delivery import post_twilio_sms, send_twilio_sms_report


class _Resp:
    def __init__(self, payload: dict[str, Any], status_code: int = 201) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


def _patch_post(monkeypatch: pytest.MonkeyPatch, response: _Resp) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _Resp:
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr("app.utils.twilio_delivery.httpx.post", _fake_post)
    return captured


def test_post_twilio_sms_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_post(monkeypatch, _Resp({"sid": "SM1"}))

    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="ping",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
    )

    assert (success, error, sid) == (True, "", "SM1")
    assert captured["url"].endswith("/Accounts/AC1/Messages.json")
    assert captured["data"]["To"] == "+14155550000"
    assert captured["data"]["From"] == "+14155551111"
    assert captured["data"]["Body"] == "ping"
    assert "MessagingServiceSid" not in captured["data"]
    assert "StatusCallback" not in captured["data"]


def test_post_twilio_sms_with_messaging_service_overrides_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_post(monkeypatch, _Resp({"sid": "SM2"}))

    post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
        messaging_service_sid="MG123",
    )

    assert captured["data"]["MessagingServiceSid"] == "MG123"
    assert "From" not in captured["data"]


def test_post_twilio_sms_status_callback_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_post(monkeypatch, _Resp({"sid": "SM3"}))

    post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
        status_callback="https://example.com/webhooks/twilio/status",
    )

    assert captured["data"]["StatusCallback"] == "https://example.com/webhooks/twilio/status"


def test_post_twilio_sms_missing_sender_fails() -> None:
    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
    )

    assert success is False
    assert "from_number" in error
    assert sid == ""


def test_post_twilio_sms_transport_failure_redacts_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("auth header tok-leak failed")

    monkeypatch.setattr("app.utils.twilio_delivery.httpx.post", _fake_post)

    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok-leak",
        from_number="+14155551111",
    )

    assert success is False
    assert "tok-leak" not in error
    assert "<redacted>" in error
    assert sid == ""


def test_post_twilio_sms_api_error_returns_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _Resp({"message": "Invalid 'To' parameter"}, status_code=400))

    success, error, sid = post_twilio_sms(
        to="bad",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
    )

    assert success is False
    assert "Invalid 'To' parameter" in error
    assert sid == ""


def test_send_twilio_sms_report_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_post(monkeypatch, _Resp({"sid": "SM6"}))

    send_twilio_sms_report(
        report="X" * 5000,
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "from_number": "+14155551111",
            "to": "+14155550000",
        },
    )

    assert len(captured["data"]["Body"]) <= 1600
    assert captured["data"]["Body"].endswith("…")


def test_send_twilio_sms_report_missing_creds() -> None:
    success, error, sid = send_twilio_sms_report(
        report="hi",
        sms_ctx={"account_sid": "AC1"},
    )

    assert success is False
    assert "Missing" in error
    assert sid == ""


def test_send_twilio_sms_report_missing_sender() -> None:
    success, error, sid = send_twilio_sms_report(
        report="hi",
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "to": "+14155550000",
        },
    )

    assert success is False
    assert "from_number" in error
    assert sid == ""


def test_send_twilio_sms_report_success_returns_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _Resp({"sid": "SM-OK"}))

    success, error, sid = send_twilio_sms_report(
        report="investigation summary",
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "messaging_service_sid": "MG1",
            "to": "+14155550000",
        },
    )

    assert success is True
    assert error == ""
    assert sid == "SM-OK"
