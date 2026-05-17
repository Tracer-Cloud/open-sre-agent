"""Tests for app/utils/whatsapp_delivery.py."""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.whatsapp_delivery import (
    post_whatsapp_message,
    send_whatsapp_report,
)


class _FakeDeliveryResponse:
    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        data: dict[str, Any] | None = None,
        text: str = "",
        error: str = "",
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self.data = data or {}
        self.text = text
        self.error = error


def test_post_whatsapp_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_post_json(
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        calls.append({"url": url, "payload": payload, "headers": headers})
        return _FakeDeliveryResponse(
            status_code=200,
            data={"messages": [{"id": "wamid.123"}]},
        )

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_json", _fake_post_json)

    success, error, message_id = post_whatsapp_message(
        to="+1234567890",
        text="Test message",
        phone_number_id="pnid-123",
        access_token="tok-123",
    )

    assert success is True
    assert error == ""
    assert message_id == "wamid.123"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://graph.facebook.com/v18.0/pnid-123/messages"
    assert calls[0]["headers"] == {"Authorization": "Bearer tok-123"}
    assert calls[0]["payload"]["to"] == "+1234567890"
    assert calls[0]["payload"]["type"] == "text"
    assert calls[0]["payload"]["text"]["body"] == "Test message"


def test_post_whatsapp_message_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post_json(*args: Any, **kwargs: Any) -> Any:
        return _FakeDeliveryResponse(ok=False, error="Connection refused")

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_json", _fake_post_json)

    success, error, message_id = post_whatsapp_message(
        to="+1234567890",
        text="Test",
        phone_number_id="pnid-123",
        access_token="tok-123",
    )

    assert success is False
    assert "Connection refused" in error
    assert message_id == ""


def test_post_whatsapp_message_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post_json(*args: Any, **kwargs: Any) -> Any:
        return _FakeDeliveryResponse(
            ok=True,
            status_code=400,
            data={"error": {"message": "Invalid phone number"}},
            text="Bad Request",
        )

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_json", _fake_post_json)

    success, error, message_id = post_whatsapp_message(
        to="+1234567890",
        text="Test",
        phone_number_id="pnid-123",
        access_token="tok-123",
    )

    assert success is False
    assert "Invalid phone number" in error
    assert message_id == ""


def test_post_whatsapp_message_redacts_token_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _fake_post_json(*args: Any, **kwargs: Any) -> Any:
        return _FakeDeliveryResponse(
            ok=True,
            status_code=400,
            data={
                "error": {
                    "message": "Error for url: https://graph.facebook.com/v18.0/pnid-123/messages?access_token=SECRET"
                }
            },
        )

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_json", _fake_post_json)
    caplog.set_level("WARNING", logger="app.utils.whatsapp_delivery")

    post_whatsapp_message(
        to="+123",
        text="Test",
        phone_number_id="pnid-123",
        access_token="SECRET",
    )

    assert "SECRET" not in caplog.text


def test_send_whatsapp_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(
        to: str,
        text: str,
        phone_number_id: str,
        access_token: str,
    ) -> tuple[bool, str, str]:
        return True, "", "wamid.456"

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_whatsapp_message", _fake_post)

    success, error = send_whatsapp_report(
        report="Investigation summary",
        whatsapp_ctx={
            "access_token": "tok",
            "phone_number_id": "pnid",
            "to": "+123",
        },
    )

    assert success is True
    assert error == ""


def test_send_whatsapp_report_missing_credentials() -> None:
    success, error = send_whatsapp_report(
        report="Test",
        whatsapp_ctx={"access_token": "tok"},
    )

    assert success is False
    assert "Missing" in error


def test_send_whatsapp_report_truncates_long_report(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_text: str = ""

    def _fake_post(
        to: str,
        text: str,
        phone_number_id: str,
        access_token: str,
    ) -> tuple[bool, str, str]:
        nonlocal captured_text
        captured_text = text
        return True, "", "wamid.789"

    monkeypatch.setattr("app.utils.whatsapp_delivery.post_whatsapp_message", _fake_post)

    send_whatsapp_report(
        report="X" * 5000,
        whatsapp_ctx={
            "access_token": "tok",
            "phone_number_id": "pnid",
            "to": "+123",
        },
    )

    assert len(captured_text) <= 4096
    assert captured_text.endswith("…")
