from __future__ import annotations

from unittest.mock import MagicMock

from gateway.transports.buzz.poller.poller import BuzzFeedPoller
from integrations.buzz.client import BuzzClient


def _event(event_id: str, *, created_at: int, channel: str = "chan-1", pubkey: str = "pk-1"):
    return {
        "id": event_id,
        "pubkey": pubkey,
        "content": "hi",
        "created_at": created_at,
        "tags": [["h", channel]],
    }


def test_poll_once_parses_events_and_advances_cursor() -> None:
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = {
        "success": True,
        "error": "",
        "events": [_event("ev1", created_at=100), _event("ev2", created_at=200)],
    }
    poller = BuzzFeedPoller(client)

    events = poller.poll_once()

    assert [e.event_id for e in events] == ["ev1", "ev2"]
    client.get_feed.assert_called_once_with(since=0, types="mentions")
    assert poller._since == 200


def test_poll_once_dedupes_events_at_the_same_inclusive_cursor() -> None:
    """``since`` is inclusive, so the same event would replay forever without this."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.side_effect = [
        {"success": True, "error": "", "events": [_event("ev1", created_at=100)]},
        {"success": True, "error": "", "events": [_event("ev1", created_at=100)]},
    ]
    poller = BuzzFeedPoller(client)

    first = poller.poll_once()
    second = poller.poll_once()

    assert [e.event_id for e in first] == ["ev1"]
    assert second == []


def test_poll_once_returns_empty_list_on_fetch_failure() -> None:
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = {"success": False, "error": "relay unreachable", "events": []}
    poller = BuzzFeedPoller(client)

    assert poller.poll_once() == []
    assert poller._since == 0


def test_poll_once_skips_malformed_events() -> None:
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = {
        "success": True,
        "error": "",
        "events": ["not-a-dict", {"id": "no-pubkey", "tags": []}],
    }
    poller = BuzzFeedPoller(client)

    assert poller.poll_once() == []
