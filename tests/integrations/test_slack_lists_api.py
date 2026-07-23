"""Tests for Slack Lists discovery + row read helpers."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.web_client as web_client


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
def target() -> web_client.SlackBotTarget:
    return web_client.SlackBotTarget(bot_token="xoxb-test")


def _install(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _FakeClient:
    client = _FakeClient(script)
    monkeypatch.setattr(web_client, "_shared_client", lambda: client)
    return client


def test_find_slack_lists_filters_filetype_list_and_name(
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
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

    found, err = web_client.find_slack_lists(target, name_query="team tasks", limit=10)

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
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
) -> None:
    _install(monkeypatch, [_FakeResponse(200, {"ok": False, "error": "missing_scope"})])

    found, err = web_client.find_slack_lists(target, name_query="tasks")

    assert found is None
    assert "files:read" in err


def test_fetch_slack_list_items_normalizes_rows(
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
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

    items, err, truncated = web_client.fetch_slack_list_items(target, list_id="FTASKS123", limit=10)

    assert err == ""
    assert truncated is False
    assert items is not None
    assert len(items) == 1
    row = items[0]
    assert row["id"] == "Rec1"
    assert row["name"] == "[HIGH] Stripe payment"
    assert row["assignees"] == ["UVAIBHAV"]
    assert row["status"] == "in_progress"
    assert row["due_date"] == "2026-07-20"


def test_fetch_slack_list_items_rejects_bad_id(target: web_client.SlackBotTarget) -> None:
    items, err, _truncated = web_client.fetch_slack_list_items(target, list_id="C12345678")
    assert items is None
    assert "F…" in err or "F..." in err or "F" in err


def test_fetch_slack_list_items_missing_lists_scope(
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
) -> None:
    _install(monkeypatch, [_FakeResponse(200, {"ok": False, "error": "missing_scope"})])

    items, err, _truncated = web_client.fetch_slack_list_items(target, list_id="FABCDEFGH1")

    assert items is None
    assert "lists:read" in err


def test_fetch_slack_list_items_uppercases_lowercase_list_id(
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
) -> None:
    # Arrange: a structurally valid but lowercase id must be accepted (uppercased),
    # not rejected with the "must be a Slack List id" error.
    client = _install(
        monkeypatch,
        [_FakeResponse(200, {"ok": True, "items": [], "response_metadata": {"next_cursor": ""}})],
    )

    # Act
    items, err, _truncated = web_client.fetch_slack_list_items(target, list_id="ftasks123")

    # Assert: the call proceeded and sent the uppercased id.
    assert err == ""
    assert items == []
    assert client.calls[0][2]["json"]["list_id"] == "FTASKS123"


def test_fetch_slack_list_items_flags_truncation_at_page_bound(
    monkeypatch: pytest.MonkeyPatch, target: web_client.SlackBotTarget
) -> None:
    # Arrange: every page returns one row and always advertises another cursor,
    # so the list never exhausts within the page safety bound.
    one_more_page = _FakeResponse(
        200,
        {
            "ok": True,
            "items": [{"id": "R", "list_id": "FTASKS123", "fields": []}],
            "response_metadata": {"next_cursor": "more"},
        },
    )
    _install(monkeypatch, [one_more_page] * (web_client._MAX_LIST_ITEM_PAGES + 2))

    # Act: request more rows than the bounded pages can supply.
    items, err, truncated = web_client.fetch_slack_list_items(
        target, list_id="FTASKS123", limit=web_client._MAX_LIST_ITEMS
    )

    # Assert: a partial read is reported as truncated, not as the whole list.
    assert err == ""
    assert items is not None
    assert len(items) == web_client._MAX_LIST_ITEM_PAGES
    assert truncated is True
