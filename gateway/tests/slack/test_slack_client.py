"""Unit tests for SlackWebApiClient without live Slack."""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError

from gateway.slack.client import SlackWebApiClient


class _FakeWebClient:
    def __init__(
        self, *, post: dict[str, Any] | Exception, update: Exception | None = None
    ) -> None:
        self._post = post
        self._update = update
        self.post_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append(kwargs)
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        if self._update is not None:
            raise self._update
        return {"ok": True}


def _api_error(code: str) -> SlackApiError:
    response = {"ok": False, "error": code}
    return SlackApiError(message=code, response=response)


def test_post_message_returns_ts() -> None:
    web = _FakeWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi", thread_ts="1.0") == "1.2"
    assert web.post_calls[0]["channel"] == "C1"


def test_post_message_returns_none_on_api_error() -> None:
    web = _FakeWebClient(post=_api_error("channel_not_found"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi") is None


def test_update_message_false_on_api_error() -> None:
    web = _FakeWebClient(post={"ts": "1.2"}, update=_api_error("message_not_found"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.update_message(channel="C1", ts="1.2", text="x") is False


def test_update_message_true_on_success() -> None:
    web = _FakeWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.update_message(channel="C1", ts="1.2", text="done") is True
