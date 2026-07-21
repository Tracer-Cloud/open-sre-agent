"""Tests for Slack Lists discovery + row read helpers."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.bot_api as bot_api
from integrations.slack.bot_api import SlackBotTarget


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, path: str, **kw: Any) -> _FakeResponse:
        self.calls.append(("GET", path, kw))
        return self._next()

    def post(self, path: str, **kw: Any) -> _FakeResponse:
        self.calls.append(("POST", path, kw))
        return self._next()

    def _next(self) -> _FakeResponse:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def target() -> SlackBotTarget:
    return SlackBotTarget(bot_token="xoxb-test")


def _install(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _FakeClient:
    client = _FakeClient(script)
    monkeypatch.setattr(bot_api, "_shared_client", lambda: client)
    return client


def test_find_slack_lists_filters_filetype_list_and_name(
    monkeypatch: pytest.MonkeyPatch, target: SlackBotTarget
) -> None:
    _install(
        monkeypatch,
        [
            _FakeResponse(
                200,
                {
                    "ok": True,
                    "files": [
                        {
                            "id": "FIMG",
                            "filetype": "png",
                            "name": "shot.png",
                            "title": "shot",
                        },
                        {
                            "id": "FTASKS",
                            "filetype": "list",
                            "name": "opensre-team-tasks",
                            "title": "OpenSRE Team Tasks",
                            "permalink": "https://slack.com/lists/FTASKS",
                        },
                        {
                            "id": "FOTHER",
                            "filetype": "list",
                            "name": "hackathon",
                            "title": "Hackathon Week",
                        },
                    ],
                    "paging": {"pages": 1},
                },
            )
        ],
    )

    found, err = bot_api.find_slack_lists(target, name_query="team tasks", limit=10)

    assert err == ""
    assert found == [
        {
            "list_id": "FTASKS",
            "name": "opensre-team-tasks",
            "title": "OpenSRE Team Tasks",
            "permalink": "https://slack.com/lists/FTASKS",
        }
    ]


def test_find_slack_lists_missing_scope_hint(
    monkeypatch: pytest.MonkeyPatch, target: SlackBotTarget
) -> None:
    _install(monkeypatch, [_FakeResponse(200, {"ok": False, "error": "missing_scope"})])

    found, err = bot_api.find_slack_lists(target, name_query="tasks")

    assert found is None
    assert "files:read" in err


def test_fetch_slack_list_items_normalizes_rows(
    monkeypatch: pytest.MonkeyPatch, target: SlackBotTarget
) -> None:
    _install(
        monkeypatch,
        [
            _FakeResponse(
                200,
                {
                    "ok": True,
                    "items": [
                        {
                            "id": "Rec1",
                            "list_id": "FTASKS123",
                            "archived": False,
                            "fields": [
                                {
                                    "key": "name",
                                    "text": "[HIGH] Stripe payment",
                                    "column_id": "ColName",
                                },
                                {
                                    "key": "owner",
                                    "user": ["UVAIBHAV"],
                                    "column_id": "ColOwner",
                                },
                                {
                                    "key": "status",
                                    "select": ["in_progress"],
                                    "column_id": "ColStatus",
                                },
                                {
                                    "key": "due",
                                    "date": ["2026-07-20"],
                                    "column_id": "ColDue",
                                },
                            ],
                        }
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        ],
    )

    items, err = bot_api.fetch_slack_list_items(target, list_id="FTASKS123", limit=10)

    assert err == ""
    assert items is not None
    assert len(items) == 1
    row = items[0]
    assert row["id"] == "Rec1"
    assert row["name"] == "[HIGH] Stripe payment"
    assert row["assignees"] == ["UVAIBHAV"]
    assert row["status"] == "in_progress"
    assert row["due_date"] == "2026-07-20"


def test_fetch_slack_list_items_rejects_bad_id(target: SlackBotTarget) -> None:
    items, err = bot_api.fetch_slack_list_items(target, list_id="C12345678")
    assert items is None
    assert "F…" in err or "F..." in err or "F" in err


def test_fetch_slack_list_items_missing_lists_scope(
    monkeypatch: pytest.MonkeyPatch, target: SlackBotTarget
) -> None:
    _install(monkeypatch, [_FakeResponse(200, {"ok": False, "error": "missing_scope"})])

    items, err = bot_api.fetch_slack_list_items(target, list_id="FABCDEFGH1")

    assert items is None
    assert "lists:read" in err
