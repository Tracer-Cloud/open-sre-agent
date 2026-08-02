"""Tests for integrations/mattermost/delivery.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations.mattermost import delivery as mattermost_delivery
from integrations.mattermost.delivery import (
    post_mattermost_message,
    post_mattermost_webhook,
    send_mattermost_report,
)
from platform.notifications.delivery_transport import DeliveryResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER = "https://chat.example.com"


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _ok_body(message_id: str = "msg-123") -> dict[str, Any]:
    return {"id": message_id, "channel_id": "chan-1", "message": "hello"}


# ---------------------------------------------------------------------------
# post_mattermost_message
# ---------------------------------------------------------------------------


def test_post_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(201, _ok_body()),
    )
    ok, error, message_id = post_mattermost_message(_SERVER, "chan-1", "hello", "tok")
    assert ok is True
    assert error == ""
    assert message_id == "msg-123"


def test_post_message_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any
    ) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mock_response(201, _ok_body())

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    attachments = [{"title": "Test"}]
    post_mattermost_message(f"{_SERVER}/", "chan-1", "hello", "tok", attachments=attachments)

    assert captured["url"] == f"{_SERVER}/api/v4/posts"
    assert captured["json"]["channel_id"] == "chan-1"
    assert captured["json"]["message"] == "hello"
    assert captured["json"]["props"]["attachments"] == attachments
    assert captured["headers"] == {"Authorization": "Bearer tok"}


def test_post_message_omits_props_when_no_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(201, _ok_body())

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    post_mattermost_message(_SERVER, "chan-1", "hello", "tok")
    assert "props" not in captured["json"]


def test_post_message_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(404, {"message": "Channel does not exist."}),
    )
    ok, error, message_id = post_mattermost_message(_SERVER, "chan-nope", "hello", "tok")
    assert ok is False
    assert "Channel does not exist." in error
    assert message_id == ""


def test_post_message_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _raise)
    ok, error, message_id = post_mattermost_message(_SERVER, "chan-1", "hello", "tok")
    assert ok is False
    assert "network down" in error
    assert message_id == ""


def test_post_message_handles_html_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.mattermost.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(
            ok=True,
            status_code=502,
            data={},
            text="<html>Bad Gateway</html>",
        ),
    )
    ok, error, message_id = post_mattermost_message(_SERVER, "chan-1", "hi", "tok")
    assert ok is False
    assert "<html>Bad Gateway</html>" in error
    assert message_id == ""


# ---------------------------------------------------------------------------
# Shared-transport delegation
# ---------------------------------------------------------------------------


class TestDelegatesToSharedTransport:
    """The Mattermost helper must go through ``delivery_transport.post_json``
    rather than calling httpx directly, matching the other messaging vendors."""

    def test_module_does_not_import_httpx(self) -> None:
        assert not hasattr(mattermost_delivery, "httpx"), (
            "mattermost delivery should not import httpx directly — "
            "it must go through delivery_transport.post_json"
        )

    def test_post_message_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            calls.append({"url": url, "payload": payload, **kw})
            return DeliveryResponse(ok=True, status_code=201, data=_ok_body("m-via-helper"))

        monkeypatch.setattr("integrations.mattermost.delivery.post_json", _stub_post_json)
        ok, _err, mid = post_mattermost_message(_SERVER, "chan-1", "hi", "tok")
        assert ok is True
        assert mid == "m-via-helper"
        assert calls and calls[0]["url"].endswith("/api/v4/posts")
        assert calls[0]["headers"] == {"Authorization": "Bearer tok"}


# ---------------------------------------------------------------------------
# Token redaction
# ---------------------------------------------------------------------------


class TestMattermostExceptionRedaction:
    def test_exception_error_redacts_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = "mm-secret-token-abc123"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "integrations.mattermost.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        ok, error, message_id = post_mattermost_message(_SERVER, "chan-1", "hi", token)
        assert ok is False
        assert token not in error
        assert "<redacted>" in error
        assert message_id == ""

    def test_api_error_redacts_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = "mm-secret-token-abc123"
        monkeypatch.setattr(
            "integrations.mattermost.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(
                ok=True,
                status_code=401,
                data={"message": f"bad token {token}"},
            ),
        )
        ok, error, _ = post_mattermost_message(_SERVER, "chan-1", "hi", token)
        assert ok is False
        assert token not in error
        assert "<redacted>" in error

    def test_exception_log_redacts_token(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = "mm-secret-token-abc123"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "integrations.mattermost.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        with caplog.at_level(logging.WARNING, logger="integrations.mattermost.delivery"):
            post_mattermost_message(_SERVER, "chan-1", "hi", token)

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert token not in joined
        assert "<redacted>" in joined


# ---------------------------------------------------------------------------
# send_mattermost_report
# ---------------------------------------------------------------------------

_CTX = {
    "server_url": _SERVER,
    "channel": "chan-1",
    "auth_token": "tok",
}


def test_send_report_posts_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        return _mock_response(201, _ok_body())

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    ok, error = send_mattermost_report("Report text", _CTX)

    assert ok is True
    assert error == ""
    assert captured["json"]["channel_id"] == "chan-1"
    attachment = captured["json"]["props"]["attachments"][0]
    assert attachment["title"] == "Investigation Complete"
    assert attachment["text"] == "Report text"
    assert attachment["color"] == "#E74C3C"


def test_send_report_returns_false_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(403, {"message": "unauthorized"}),
    )
    ok, error = send_mattermost_report("Report", _CTX)
    assert ok is False
    assert "unauthorized" in error


def test_send_report_truncates_text_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **kw: (
            captured.update({"props": kw["json"].get("props", {})})
            or _mock_response(201, _ok_body())
        ),  # type: ignore[misc]
    )
    long_report = "x" * 5000
    send_mattermost_report(long_report, _CTX)
    text = captured["props"]["attachments"][0]["text"]
    assert len(text) == 4096
    assert text.endswith("…")


# ---------------------------------------------------------------------------
# post_mattermost_webhook
# ---------------------------------------------------------------------------

_WEBHOOK = f"{_SERVER}/hooks/hook-id"


def test_post_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "ok"
        return resp

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    attachments = [{"title": "Test"}]
    ok, error = post_mattermost_webhook(_WEBHOOK, "hello", attachments=attachments)

    assert ok is True
    assert error == ""
    assert captured["url"] == _WEBHOOK
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["attachments"] == attachments


def test_post_webhook_omits_attachments_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "ok"
        return resp

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    post_mattermost_webhook(_WEBHOOK, "hello")
    assert "attachments" not in captured["json"]


def test_post_webhook_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(400, {"message": "Invalid webhook"}),
    )
    ok, error = post_mattermost_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert "Invalid webhook" in error


def test_post_webhook_non_200_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(410, {}),
    )
    ok, _error = post_mattermost_webhook(_WEBHOOK, "hello")
    assert ok is False


def test_post_webhook_exception_redacts_url(monkeypatch: pytest.MonkeyPatch) -> None:
    leak_msg = f"connect failed for {_WEBHOOK}"
    monkeypatch.setattr(
        "integrations.mattermost.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
    )
    ok, error = post_mattermost_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert _WEBHOOK not in error
    assert "<redacted>" in error


def test_post_webhook_error_body_redacts_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.mattermost.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(
            ok=True,
            status_code=404,
            data={"message": f"no hook at {_WEBHOOK}"},
        ),
    )
    ok, error = post_mattermost_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert _WEBHOOK not in error
    assert "<redacted>" in error


# ---------------------------------------------------------------------------
# send_mattermost_report — webhook routing
# ---------------------------------------------------------------------------


def test_send_report_prefers_pat_over_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token credentials with a channel are preferred over a configured webhook.

    Matches the routing rule everywhere else in this module (alarms, the
    send-message tool, and the verifier, which probes the token endpoint
    whenever a token is configured) — preferring the webhook instead would let
    `opensre integrations verify mattermost` pass on the token while report
    delivery silently used an untested webhook.
    """
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        return _mock_response(201, _ok_body())

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    ok, error = send_mattermost_report("Report text", {**_CTX, "webhook_url": _WEBHOOK})

    assert ok is True
    assert error == ""
    assert captured["url"] == f"{_SERVER}/api/v4/posts"
    assert captured["json"]["channel_id"] == "chan-1"
    attachment = captured["json"]["props"]["attachments"][0]
    assert attachment["title"] == "Investigation Complete"
    assert attachment["text"] == "Report text"


def test_send_report_webhook_only_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **_kw: Any) -> MagicMock:
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "ok"
        return resp

    monkeypatch.setattr("platform.notifications.delivery_transport.httpx.post", _fake_post)
    ok, _ = send_mattermost_report("Report", {"webhook_url": _WEBHOOK})
    assert ok is True
    assert captured["url"] == _WEBHOOK


def test_send_report_webhook_failure_propagates_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(400, {"message": "disabled"}),
    )
    ok, error = send_mattermost_report("Report", {"webhook_url": _WEBHOOK})
    assert ok is False
    assert "disabled" in error


def test_send_report_token_without_channel_never_falls_back_to_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token configured without a channel must not silently use the webhook.

    Mixed config: full token credentials + webhook + no channel. Token
    credentials mean channel-targeting mode, so the missing channel is a
    configuration gap to surface, not a license to deliver to the webhook's
    fixed destination — the same rule as
    ``integrations.mattermost.credentials.load_credentials_from_env`` and the
    ``mattermost_send_message`` tool. This is also the exact scenario where
    ``verify_mattermost`` reports the integration healthy (it only checks the
    token, not whether a channel is configured), so silently falling back
    here would let a "healthy" integration fail delivery unpredictably.
    """

    def _explode_webhook(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("webhook must not be used as a fallback when a token is configured")

    monkeypatch.setattr(
        "platform.notifications.delivery_transport.httpx.post",
        _explode_webhook,
    )

    ok, error = send_mattermost_report(
        "Report", {"server_url": _SERVER, "auth_token": "tok", "webhook_url": _WEBHOOK}
    )
    assert ok is False
    assert "channel" in error.lower()
