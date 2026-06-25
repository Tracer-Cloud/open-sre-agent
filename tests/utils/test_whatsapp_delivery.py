"""Tests for utils/whatsapp_delivery.py."""

from __future__ import annotations

from platform.notifications.whatsapp_delivery import (
    post_whatsapp_message_twilio,
    send_whatsapp_report,
)
from typing import Any

import pytest


def test_post_whatsapp_message_twilio_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 201
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            return {"sid": "SM123"}

    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> Any:
        captured["url"] = url
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr("platform.notifications.whatsapp_delivery.httpx.post", _fake_post)

    success, error, message_id = post_whatsapp_message_twilio(
        to="+1234567890",
        text="hello",
        account_sid="AC123",
        auth_token="secret",
        from_number="whatsapp:+14155238886",
    )

    assert success is True
    assert error == ""
    assert message_id == "SM123"
    assert captured["url"].endswith("/Accounts/AC123/Messages.json")
    assert captured["data"]["From"] == "whatsapp:+14155238886"
    assert captured["data"]["To"] == "whatsapp:+1234567890"
    assert captured["data"]["Body"] == "hello"


def test_post_whatsapp_message_twilio_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Connection refused")

    monkeypatch.setattr("platform.notifications.whatsapp_delivery.httpx.post", _fake_post)

    success, error, message_id = post_whatsapp_message_twilio(
        to="+123",
        text="test",
        account_sid="AC123",
        auth_token="tok",
        from_number="whatsapp:+14155238886",
    )

    assert success is False
    assert "Connection refused" in error
    assert message_id == ""


def test_post_whatsapp_message_twilio_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 400
        text = "Bad Request"

        @staticmethod
        def json() -> dict[str, Any]:
            return {"message": "Invalid 'From' parameter"}

    monkeypatch.setattr("platform.notifications.whatsapp_delivery.httpx.post", lambda *_a, **_kw: _Resp())

    success, error, message_id = post_whatsapp_message_twilio(
        to="+123",
        text="Test",
        account_sid="AC123",
        auth_token="tok-123",
        from_number="whatsapp:bad",
    )

    assert success is False
    assert "Invalid 'From' parameter" in error
    assert message_id == ""


def test_send_whatsapp_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(
        to: str,
        text: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> tuple[bool, str, str]:
        return True, "", "SM456"

    monkeypatch.setattr("platform.notifications.whatsapp_delivery.post_whatsapp_message_twilio", _fake_post)

    success, error = send_whatsapp_report(
        report="Investigation summary",
        whatsapp_ctx={
            "account_sid": "AC123",
            "auth_token": "tok",
            "from_number": "whatsapp:+14155238886",
            "to": "+123",
        },
    )

    assert success is True
    assert error == ""


def test_send_whatsapp_report_missing_credentials() -> None:
    success, error = send_whatsapp_report(
        report="Test",
        whatsapp_ctx={"account_sid": "AC123"},
    )

    assert success is False
    assert "Missing" in error


def test_send_whatsapp_report_truncates_long_report(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_text: str = ""

    def _fake_post(
        to: str,
        text: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> tuple[bool, str, str]:
        nonlocal captured_text
        captured_text = text
        return True, "", "SM789"

    monkeypatch.setattr("platform.notifications.whatsapp_delivery.post_whatsapp_message_twilio", _fake_post)

    send_whatsapp_report(
        report="X" * 5000,
        whatsapp_ctx={
            "account_sid": "AC123",
            "auth_token": "tok",
            "from_number": "whatsapp:+14155238886",
            "to": "+123",
        },
    )

    assert len(captured_text) <= 4096
    assert captured_text.endswith("…")
