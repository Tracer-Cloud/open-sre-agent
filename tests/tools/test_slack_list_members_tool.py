"""Tests for integrations/slack/tools/slack_list_members_tool - workspace roster."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.slack.tools.slack_list_members_tool import (
    SlackListTeamMembersTool,
    slack_list_team_members,
)
from integrations.slack.web_client import SlackBotTarget, fetch_team_members


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
    monkeypatch.setattr(
        "integrations.slack.web_client._shared_client", lambda: _FakeClient(responder)
    )


def test_metadata_is_read_only_without_approval() -> None:
    metadata = SlackListTeamMembersTool.metadata()
    assert metadata.name == "slack_list_team_members"
    assert metadata.side_effect_level == "read_only"
    assert slack_list_team_members.requires_approval is False


def test_description_steers_roster_away_from_channel_history() -> None:
    tool = SlackListTeamMembersTool()
    blob = f"{tool.description}\n" + "\n".join(tool.use_cases + tool.anti_examples)
    assert "who is on the team" in blob.lower()
    assert "slack_read_messages" in blob
    assert "channel history" in blob.lower() or "channel/thread" in blob.lower()


def test_fetch_filters_deleted_and_slackbot(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ok": True,
        "members": [
            {"id": "U1", "name": "yauhen", "profile": {"real_name": "Yauhen", "title": "Eng"}},
            {"id": "U2", "name": "gone", "deleted": True, "profile": {}},
            {"id": "USLACKBOT", "name": "slackbot", "profile": {}},
            {"id": "B1", "name": "bot", "is_bot": True, "profile": {"real_name": "Bot"}},
        ],
    }
    _install_fake_client(
        monkeypatch,
        lambda *_a, **_kw: _FakeResponse(payload),
    )

    members, error, truncated = fetch_team_members(SlackBotTarget(bot_token="xoxb-x"))

    assert error == ""
    assert truncated is False
    assert members is not None
    assert [m["id"] for m in members] == ["U1", "B1"]
    assert members[0]["title"] == "Eng"


def test_fetch_signals_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_get(*_a: Any, **_kw: Any) -> _FakeResponse:
        calls["n"] += 1
        return _FakeResponse(
            {
                "ok": True,
                "members": [
                    {
                        "id": f"U{calls['n']}",
                        "name": f"u{calls['n']}",
                        "profile": {},
                    }
                ],
                "response_metadata": {"next_cursor": "more"},
            }
        )

    _install_fake_client(monkeypatch, fake_get)

    members, error, truncated = fetch_team_members(SlackBotTarget(bot_token="xoxb-x"))

    assert error == ""
    assert truncated is True
    assert members is not None
    assert len(members) == 5


def test_run_excludes_bots_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    roster = [
        {
            "id": "U1",
            "username": "y",
            "real_name": "Y",
            "display_name": "",
            "title": "",
            "is_bot": False,
        },
        {
            "id": "B1",
            "username": "b",
            "real_name": "B",
            "display_name": "",
            "title": "",
            "is_bot": True,
        },
    ]
    monkeypatch.setattr(
        "integrations.slack.tools.slack_list_members_tool.tool.fetch_team_members",
        lambda *_a, **_kw: (list(roster), "", False),
    )

    default = SlackListTeamMembersTool().run()
    with_bots = SlackListTeamMembersTool().run(include_bots=True)

    assert default["member_count"] == 1
    assert default["truncated"] is False
    assert with_bots["member_count"] == 2
    assert "error" not in default


def test_is_available_with_nested_config_source() -> None:
    tool = SlackListTeamMembersTool()
    assert tool.is_available({"slack": {"config": {"bot_token": "xoxb-nested"}}}) is True


def test_is_available_falls_back_to_store_when_sources_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        "integrations.slack.web_client.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-from-store"), ""),
    )
    assert SlackListTeamMembersTool().is_available({}) is True


def test_missing_scope_maps_to_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        lambda *_a, **_kw: _FakeResponse({"ok": False, "error": "missing_scope"}),
    )

    members, error, truncated = fetch_team_members(SlackBotTarget(bot_token="xoxb-x"))

    assert members is None
    assert truncated is False
    assert "users:read" in error
