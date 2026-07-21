"""Unit tests for slack_read_list tool resolution + read path."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.slack.tools.slack_read_list_tool.tool import SlackReadListTool
from integrations.slack.web_client import SlackBotTarget


@pytest.fixture
def tool() -> SlackReadListTool:
    return SlackReadListTool()


def test_reads_items_when_list_id_provided(
    monkeypatch: pytest.MonkeyPatch, tool: SlackReadListTool
) -> None:
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-x"), ""),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.fetch_slack_list_items",
        lambda _t, **_kw: (
            [
                {
                    "id": "Rec1",
                    "list_id": "FABCDEF1",
                    "name": "Task A",
                    "assignees": ["U1"],
                    "status": "",
                    "due_date": "",
                    "archived": False,
                    "fields": {},
                }
            ],
            "",
            False,
        ),
    )

    result = tool.run(list_id="FABCDEF1", limit=10)

    assert result["status"] == "read"
    assert result["list_id"] == "FABCDEF1"
    assert result["item_count"] == 1
    assert result["items"][0]["name"] == "Task A"


def test_discovers_single_list_by_name(
    monkeypatch: pytest.MonkeyPatch, tool: SlackReadListTool
) -> None:
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-x"), ""),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.find_slack_lists",
        lambda _t, **_kw: (
            [
                {
                    "list_id": "FTASKS1",
                    "name": "opensre-team-tasks",
                    "title": "OpenSRE Team Tasks",
                    "permalink": "",
                }
            ],
            "",
        ),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.fetch_slack_list_items",
        lambda _t, **kw: (
            [
                {
                    "id": "Rec1",
                    "list_id": kw["list_id"],
                    "name": "Row",
                    "assignees": [],
                    "status": "",
                    "due_date": "",
                    "archived": False,
                    "fields": {},
                }
            ],
            "",
            False,
        ),
    )

    result = tool.run(name_query="OpenSRE Team Tasks")

    assert result["status"] == "read"
    assert result["list_id"] == "FTASKS1"
    assert result["list_title"] == "OpenSRE Team Tasks"
    assert result["item_count"] == 1


def test_multiple_matches_returns_candidates_without_guessing(
    monkeypatch: pytest.MonkeyPatch, tool: SlackReadListTool
) -> None:
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-x"), ""),
    )
    candidates = [
        {"list_id": "F1AAAA", "name": "a", "title": "Team Tasks A", "permalink": ""},
        {"list_id": "F2BBBB", "name": "b", "title": "Team Tasks B", "permalink": ""},
    ]
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.find_slack_lists",
        lambda _t, **_kw: (candidates, ""),
    )
    called: list[Any] = []

    def _should_not_fetch(*_a: Any, **_k: Any) -> tuple[None, str, bool]:
        called.append(True)
        return None, "should not be called", False

    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.fetch_slack_list_items",
        _should_not_fetch,
    )

    result = tool.run(name_query="Team Tasks")

    assert result["status"] == "read"
    assert result["lists"] == candidates
    assert result["item_count"] == 0
    assert not called
    assert "Multiple" in result.get("error", "")


def test_env_default_list_id(monkeypatch: pytest.MonkeyPatch, tool: SlackReadListTool) -> None:
    monkeypatch.setenv("SLACK_TEAM_TASKS_LIST_ID", "FENVLIST1")
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-x"), ""),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_read_list_tool.tool.fetch_slack_list_items",
        lambda _t, **kw: ([], "", False) if kw["list_id"] == "FENVLIST1" else (None, "bad", False),
    )

    result = tool.run()

    assert result["status"] == "read"
    assert result["list_id"] == "FENVLIST1"
