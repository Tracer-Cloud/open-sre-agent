from __future__ import annotations

from typing import Any
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


def _feed(*events: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "error": "", "events": list(events)}


def test_acknowledging_every_event_advances_the_cursor() -> None:
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(
        _event("ev1", created_at=100), _event("ev2", created_at=200)
    )
    poller = BuzzFeedPoller(client)

    events = poller.poll_once()

    assert [e.event_id for e in events] == ["ev1", "ev2"]
    client.get_feed.assert_called_once_with(since=0, types="mentions")
    assert poller._since == 0  # fetched is not handled

    for event in events:
        poller.acknowledge(event)

    assert poller._since == 200
    assert poller.inflight_count() == 0


def test_cursor_waits_for_the_oldest_unfinished_turn() -> None:
    """A slow older turn must hold the cursor back even once newer ones finish."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(
        _event("slow", created_at=100), _event("fast", created_at=200)
    )
    poller = BuzzFeedPoller(client)
    slow, fast = poller.poll_once()

    poller.acknowledge(fast)
    assert poller._since == 0  # `slow` is still running

    poller.acknowledge(slow)
    assert poller._since == 200


def test_unacknowledged_events_are_not_refetched_while_in_flight() -> None:
    """Re-polling mid-turn must not dispatch the same mention twice."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(_event("ev1", created_at=100))
    poller = BuzzFeedPoller(client)

    first = poller.poll_once()
    second = poller.poll_once()

    assert [e.event_id for e in first] == ["ev1"]
    assert second == []
    assert poller.inflight_count() == 1


def test_never_acknowledged_events_replay_on_a_fresh_poller() -> None:
    """The crash/shutdown guarantee: unfinished work is re-delivered, not skipped."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(_event("ev1", created_at=100))
    poller = BuzzFeedPoller(client)
    poller.poll_once()  # dispatched, never acknowledged

    restarted = BuzzFeedPoller(client)

    assert [e.event_id for e in restarted.poll_once()] == ["ev1"]


def test_acknowledged_events_do_not_replay_at_the_inclusive_cursor() -> None:
    """``since`` is inclusive, so a handled event would replay forever without dedup."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(_event("ev1", created_at=100))
    poller = BuzzFeedPoller(client)

    first = poller.poll_once()
    poller.acknowledge(first[0])

    assert poller.poll_once() == []
    assert client.get_feed.call_args_list[-1].kwargs["since"] == 100


def test_acknowledged_events_do_not_replay_after_restart() -> None:
    """Inclusive-second IDs are persisted so a restart does not re-run finished work."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(_event("ev1", created_at=100))
    poller = BuzzFeedPoller(client)
    first = poller.poll_once()
    poller.acknowledge(first[0])

    restarted = BuzzFeedPoller(client)
    assert restarted.poll_once() == []
    assert restarted._since == 100
    assert "ev1" in restarted._acked_at_cursor
