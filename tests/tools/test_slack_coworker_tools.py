"""Tests for new Slack coworker bot_api helpers and tools."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.slack.bot_api import (
    SlackBotTarget,
    add_reaction,
    join_channel,
    search_messages,
)
from integrations.slack.tools.slack_join_channel_tool import slack_join_channel
from integrations.slack.tools.slack_search_messages_tool import slack_search_messages


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_join_channel_treats_already_in_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.slack.bot_api.httpx.post",
        lambda *_a, **_kw: _FakeResponse({"ok": False, "error": "already_in_channel"}),
    )
    ok, error = join_channel(SlackBotTarget(bot_token="xoxb-x"), channel_id="C01234567")
    assert ok is True
    assert error == ""


def test_search_messages_maps_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ok": True,
        "messages": {
            "matches": [
                {
                    "text": "boom",
                    "user": "U1",
                    "ts": "1.0",
                    "permalink": "https://example",
                    "channel": {"id": "C01234567"},
                }
            ]
        },
    }
    monkeypatch.setattr(
        "integrations.slack.bot_api.httpx.get",
        lambda *_a, **_kw: _FakeResponse(payload),
    )
    matches, error = search_messages(SlackBotTarget(bot_token="xoxb-x"), query="boom")
    assert error == ""
    assert matches is not None
    assert matches[0]["channel_id"] == "C01234567"


def test_add_reaction_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.slack.bot_api.httpx.post",
        lambda *_a, **_kw: _FakeResponse({"ok": True}),
    )
    ok, error = add_reaction(
        SlackBotTarget(bot_token="xoxb-x"),
        channel_id="C01234567",
        timestamp="1.0",
        emoji="eyes",
    )
    assert ok is True
    assert error == ""


def test_search_tool_requires_query() -> None:
    result = slack_search_messages.run(query="  ")
    assert result["status"] == "failed"


def test_join_tool_metadata() -> None:
    assert slack_join_channel.requires_approval is True
