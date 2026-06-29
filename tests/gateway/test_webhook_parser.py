from __future__ import annotations

import pytest

from gateway.platforms.telegram.webhook import parse_update


def test_parse_private_text_message() -> None:
    event = parse_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "hello",
            },
        }
    )
    assert event is not None
    assert event.user_id == "42"
    assert event.text == "hello"


def test_parse_callback_query() -> None:
    event = parse_update(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cq1",
                "from": {"id": 42},
                "data": "approve:abc",
                "message": {"message_id": 3, "chat": {"id": 42, "type": "private"}},
            },
        }
    )
    assert event is not None
    assert event.callback_query_id == "cq1"
    assert event.callback_data == "approve:abc"


def test_ignores_group_messages() -> None:
    event = parse_update(
        {
            "update_id": 3,
            "message": {
                "message_id": 1,
                "from": {"id": 42},
                "chat": {"id": -1001, "type": "group"},
                "text": "hello",
            },
        }
    )
    assert event is None


@pytest.mark.xfail(
    strict=True,
    reason="bug: int(update_id) is outside any guard, so a non-integer update_id "
    "raises ValueError out of parse_update instead of being handled",
)
def test_parse_tolerates_non_integer_update_id() -> None:
    event = parse_update(
        {
            "update_id": "not-an-int",
            "message": {
                "message_id": 10,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "hello",
            },
        }
    )
    assert event is not None
    assert event.text == "hello"
