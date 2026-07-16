"""Tests for integrations/slack/tools/slack_read_messages_tool - Slack channel-history read."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.slack.bot_api import SlackBotTarget, fetch_channel_messages
from integrations.slack.tools.slack_read_messages_tool import (
    SlackReadMessagesTool,
    slack_read_messages,
)
from integrations.slack.tools.slack_read_messages_tool.validation import (
    clamp_limit,
    validate_channel_id,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, responder: Any) -> None:
        self._responder = responder

    def get(self, path: str, **kw: Any) -> Any:
        return self._responder(path=path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self._responder(path=path, **kw)


def _install_fake_client(monkeypatch: Any, responder: Any) -> None:
    monkeypatch.setattr("integrations.slack.bot_api._shared_client", lambda: _FakeClient(responder))


def test_metadata_declares_read_only_slack_source() -> None:
    metadata = SlackReadMessagesTool.metadata()
    assert metadata.name == "slack_read_messages"
    assert metadata.source == "slack"
    assert metadata.side_effect_level == "read_only"
    assert slack_read_messages.requires_approval is False


def test_description_rejects_roster_questions() -> None:
    tool = SlackReadMessagesTool()
    anti = "\n".join(tool.anti_examples).lower()
    assert "who is on the team" in anti
    assert "slack_list_team_members" in anti
    assert "not a workspace member roster" in tool.description.lower()


def test_is_available_with_bot_token_in_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    # Isolate from the developer's local Slack store / env fallback.
    monkeypatch.setattr(
        "integrations.slack.bot_api.resolve_bot_token",
        lambda: (None, "not configured"),
    )
    assert slack_read_messages.is_available({"slack": {"bot_token": "xoxb-x"}}) is True
    assert slack_read_messages.is_available({}) is False


def test_is_available_falls_back_to_store_when_sources_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        "integrations.slack.bot_api.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-from-store"), ""),
    )
    assert slack_read_messages.is_available({}) is True


def test_validate_channel_id_accepts_ids_and_names() -> None:
    assert validate_channel_id("C0123ABCD")[0] is True
    assert validate_channel_id("#devs") == (True, "#devs", "")
    assert validate_channel_id("devs") == (True, "#devs", "")
    assert validate_channel_id("")[0] is False
    assert validate_channel_id("bad name")[0] is False


def test_clamp_limit_bounds() -> None:
    assert clamp_limit(None) == 50
    assert clamp_limit(0) == 1
    assert clamp_limit(1000) == 100


def test_fetch_returns_oldest_first_and_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ok": True,
        "messages": [
            {"user": "U2", "ts": "200.0", "text": "newest"},
            {"user": "U1", "ts": "100.0", "text": "x" * 3000},
        ],
    }
    _install_fake_client(
        monkeypatch,
        lambda *_a, **_kw: _FakeResponse(payload),
    )

    messages, error = fetch_channel_messages(
        SlackBotTarget(bot_token="xoxb-x"), channel_id="C1", limit=10
    )

    assert error == ""
    assert messages is not None
    assert [m["ts"] for m in messages] == ["100.0", "200.0"]
    assert messages[0]["text"].endswith("…")


def test_fetch_thread_uses_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_get(*_a: Any, **kwargs: Any) -> _FakeResponse:
        captured.append(kwargs.get("params") or {})
        return _FakeResponse(
            {"ok": True, "messages": [{"user": "U1", "ts": "1.0", "text": "parent"}]}
        )

    _install_fake_client(monkeypatch, fake_get)

    messages, error = fetch_channel_messages(
        SlackBotTarget(bot_token="xoxb-x"),
        channel_id="C1",
        limit=10,
        thread_ts="1.0",
    )

    assert error == ""
    assert messages is not None
    assert captured[0].get("ts") == "1.0"


def test_fetch_maps_api_errors_to_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        lambda *_a, **_kw: _FakeResponse({"ok": False, "error": "not_in_channel"}),
    )

    messages, error = fetch_channel_messages(
        SlackBotTarget(bot_token="xoxb-x"), channel_id="C1", limit=10
    )

    assert messages is None
    assert "/invite" in error


def test_run_success_has_no_error_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_messages_tool.tool.fetch_channel_messages",
        lambda *_a, **_kw: ([{"user": "U1", "ts": "1.0", "thread_ts": "", "text": "hi"}], ""),
    )

    result = SlackReadMessagesTool().run(channel_id="C01234567")

    assert result["status"] == "read"
    assert result["message_count"] == 1
    assert "error" not in result


def test_run_resolves_channel_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_messages_tool.tool.resolve_channel_id",
        lambda *_a, **_kw: ("C999", ""),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_messages_tool.tool.fetch_channel_messages",
        lambda *_a, **_kw: ([{"user": "U1", "ts": "1.0", "thread_ts": "", "text": "hi"}], ""),
    )

    result = SlackReadMessagesTool().run(channel_id="#devs")
    assert result["status"] == "read"
    assert result["channel_id"] == "C999"


def test_run_invalid_channel_fails_validation() -> None:
    result = SlackReadMessagesTool().run(channel_id="bad name")
    assert result["status"] == "failed"
    assert result["error_type"] == "validation_error"
