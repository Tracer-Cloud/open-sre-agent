"""Tests for the alert inbox module."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from http.client import HTTPConnection

import pytest

from app.cli.interactive_shell.alert_inbox import (
    AlertInbox,
    AlertListenerHandle,
    IncomingAlert,
    start_alert_listener,
)
from app.cli.support.errors import OpenSREError


class TestIncomingAlert:
    def test_valid_minimal(self) -> None:
        alert = IncomingAlert(text="CPU spike")
        assert alert.text == "CPU spike"

    def test_valid_full(self) -> None:
        alert = IncomingAlert.model_validate(
            {
                "text": "disk full",
                "alert_name": "DiskAlert",
                "severity": "critical",
                "source": "datadog",
                "received_at": datetime.now(UTC).isoformat(),
            }
        )
        assert alert.alert_name == "DiskAlert"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError, match="Unexpected field"):
            IncomingAlert.model_validate({"text": "x", "unknown": "y"})

    def test_text_is_required(self) -> None:
        with pytest.raises(ValueError, match="Field required"):
            IncomingAlert.model_validate({})


class TestAlertInbox:
    def test_put_and_pop(self) -> None:
        inbox = AlertInbox(maxsize=3)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        assert inbox.qsize == 2
        assert inbox.pop_nowait() is not None
        assert inbox.qsize == 1

    def test_iter_pending_drains(self) -> None:
        inbox = AlertInbox(maxsize=5)
        for i in range(3):
            inbox.put(IncomingAlert(text=f"alert {i}"))
        items = inbox.iter_pending()
        assert len(items) == 3
        assert inbox.qsize == 0

    def test_pop_nowait_returns_none_when_empty(self) -> None:
        assert AlertInbox().pop_nowait() is None

    def test_drop_oldest_on_overflow(self) -> None:
        inbox = AlertInbox(maxsize=2)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        inbox.put(IncomingAlert(text="c"))
        assert inbox.qsize == 2
        assert inbox.dropped == 1
        assert [a.text for a in inbox.iter_pending()] == ["b", "c"]


def _post(
    host: str, port: int, body: object, token: str | None = None
) -> tuple[int, dict[str, object]]:
    conn = HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", "/alerts", json.dumps(body), headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(host: str, port: int, path: str) -> tuple[int, dict[str, object]]:
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read()
    data = json.loads(raw) if raw else {}
    conn.close()
    return resp.status, data


@pytest.fixture
def inbox() -> AlertInbox:
    return AlertInbox(maxsize=3)


@pytest.fixture
def listener(inbox: AlertInbox) -> AlertListenerHandle:
    h = start_alert_listener(inbox, host="127.0.0.1", port=0)
    yield h
    h.stop()


class TestHttpListener:
    def test_healthcheck(self, listener: AlertListenerHandle) -> None:
        status, body = _get("127.0.0.1", listener._bound_port, "/healthz")
        assert status == 200
        assert body == {"status": "ok"}

    def test_post_alert_returns_202(self, listener: AlertListenerHandle) -> None:
        status, body = _post("127.0.0.1", listener._bound_port, {"text": "test"})
        assert status == 202
        assert body["queued"] is True

    def test_post_alert_queued(self, listener: AlertListenerHandle, inbox: AlertInbox) -> None:
        _post("127.0.0.1", listener._bound_port, {"text": "hello"})
        assert inbox.pop_nowait() is not None

    def test_invalid_json_returns_400(self, listener: AlertListenerHandle) -> None:
        conn = HTTPConnection("127.0.0.1", listener._bound_port, timeout=5)
        conn.request("POST", "/alerts", b"not json", {"Content-Type": "application/json"})
        resp = conn.getresponse()
        conn.close()
        assert resp.status == 400

    def test_missing_text_returns_400(self, listener: AlertListenerHandle) -> None:
        status, _ = _post("127.0.0.1", listener._bound_port, {})
        assert status == 400

    def test_auth(self) -> None:
        inbox = AlertInbox()
        handle = start_alert_listener(inbox, host="127.0.0.1", port=0, token="sekret")
        try:
            status, body = _post("127.0.0.1", handle._bound_port, {"text": "x"})
            assert status == 401
            assert body["error"] == "unauthorized"

            status, body = _post("127.0.0.1", handle._bound_port, {"text": "x"}, token="wrong")
            assert status == 401

            status, body = _post("127.0.0.1", handle._bound_port, {"text": "x"}, token="sekret")
            assert status == 202
        finally:
            handle.stop()

    def test_overflow_returns_202_with_dropped(self, listener: AlertListenerHandle) -> None:
        for i in range(3):
            _post("127.0.0.1", listener._bound_port, {"text": f"alert {i}"})
        status, body = _post("127.0.0.1", listener._bound_port, {"text": "overflow"})
        assert status == 202
        assert body["queued"] is True
        assert body["dropped"] == 1

    def test_unknown_path_returns_404(self, listener: AlertListenerHandle) -> None:
        status, _ = _get("127.0.0.1", listener._bound_port, "/unknown")
        assert status == 404

    def test_port_zero_selects_free_port(self) -> None:
        inbox = AlertInbox()
        handle = start_alert_listener(inbox, host="127.0.0.1", port=0)
        try:
            assert handle._bound_port > 0
        finally:
            handle.stop()

    def test_stop_shuts_down_server(self, listener: AlertListenerHandle) -> None:
        port = listener._bound_port
        listener.stop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            assert sock.connect_ex(("127.0.0.1", port)) != 0
        finally:
            sock.close()

    def test_bind_non_loopback_without_token_raises(self) -> None:
        with pytest.raises(OpenSREError, match="non-loopback"):
            start_alert_listener(AlertInbox(), host="0.0.0.0", port=0, token=None)
