"""Transport-layer tests for integrations.slack.web_client: retries, rate limits, reuse."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import integrations.slack.web_client as web_client


class _FakeResponse:
    def __init__(
        self, status_code: int, payload: Any = None, headers: dict[str, str] | None = None
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """Records calls and returns a scripted sequence of responses/exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls: list[tuple[str, str]] = []

    def _next(self, path: str, method: str) -> _FakeResponse:
        self.calls.append((method, path))
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, path: str, **_kw: Any) -> _FakeResponse:
        return self._next(path, "GET")

    def post(self, path: str, **_kw: Any) -> _FakeResponse:
        return self._next(path, "POST")


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_client.time, "sleep", lambda _s: None)


def _install(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _FakeClient:
    client = _FakeClient(script)
    monkeypatch.setattr(web_client, "_shared_client", lambda: client)
    return client


def test_success_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_FakeResponse(200, {"ok": True, "x": 1})])
    payload, err = web_client._request_json("GET", "auth.test", "xoxb-x")
    assert err == "" and payload == {"ok": True, "x": 1}


def test_rate_limit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
    client = _install(
        monkeypatch,
        [_FakeResponse(429, headers={"Retry-After": "0"}), _FakeResponse(200, {"ok": True})],
    )
    payload, err = web_client._request_json("GET", "conversations.list", "xoxb-x")
    assert err == "" and payload == {"ok": True}
    assert len(client.calls) == 2


def test_rate_limit_exhausted_reports_specific_error(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    _install(monkeypatch, [_FakeResponse(429, headers={"Retry-After": "0"})] * 3)
    payload, err = web_client._request_json("GET", "users.list", "xoxb-x")
    assert payload is None
    assert "rate-limited" in err


def test_timeout_retries_then_gives_specific_error(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    _install(monkeypatch, [httpx.TimeoutException("t")] * 3)
    payload, err = web_client._request_json("GET", "conversations.history", "xoxb-x")
    assert payload is None
    assert "timed out" in err


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, [_FakeResponse(403)])
    payload, err = web_client._request_json("POST", "chat.postMessage", "xoxb-x")
    assert payload is None
    assert "HTTP 403" in err
    assert len(client.calls) == 1


def test_idempotent_request_retries_on_server_error(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    # Arrange: a transient 5xx followed by success.
    client = _install(monkeypatch, [_FakeResponse(500), _FakeResponse(200, {"ok": True})])

    # Act: a read is safe to retry on an uncertain failure.
    payload, err = web_client._request_json("GET", "conversations.list", "xoxb-x")

    # Assert: it retried and returned the eventual success.
    assert err == "" and payload == {"ok": True}
    assert len(client.calls) == 2


def test_non_idempotent_write_not_retried_on_server_error(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    # Arrange: a 5xx (outcome unknown — Slack may have posted) then a would-be
    # duplicate success that must never be consumed.
    duplicate_send = _FakeResponse(200, {"ok": True, "ts": "should-not-be-sent-twice"})
    client = _install(monkeypatch, [_FakeResponse(500), duplicate_send])

    # Act: a write declares itself non-idempotent.
    payload, err = web_client._request_json("POST", "chat.postMessage", "xoxb-x", idempotent=False)

    # Assert: it stopped after the first attempt — the duplicate was not sent.
    assert payload is None
    assert "HTTP 500" in err
    assert len(client.calls) == 1


def test_shared_client_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_client, "_client", None)
    first = web_client._shared_client()
    second = web_client._shared_client()
    assert first is second


def test_thread_too_long_returns_error_not_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """A thread that never exhausts the cursor within the page cap must error,
    not return stale middle replies as if current."""
    page = _FakeResponse(
        200,
        {
            "ok": True,
            "messages": [{"user": "U1", "ts": "1.0", "text": "x"}],
            "response_metadata": {"next_cursor": "more"},
        },
    )
    _install(monkeypatch, [page] * (web_client._MAX_THREAD_PAGES + 2))
    messages, error = web_client._fetch_thread_replies(
        web_client.SlackBotTarget(bot_token="xoxb-x"), channel_id="C1", parent="1.0", limit=50
    )
    assert messages is None
    assert "too long" in error
