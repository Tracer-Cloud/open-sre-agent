from __future__ import annotations

from gateway.telegram.poller.parse_telegram_update import parse_update


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


def test_parse_callback_query_is_ignored() -> None:
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
    assert event is None


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
