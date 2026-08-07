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


def test_cursor_never_covers_the_second_of_an_unfinished_turn() -> None:
    """A slow older turn pins the watermark to its own second, not past it."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(
        _event("slow", created_at=100), _event("fast", created_at=200)
    )
    poller = BuzzFeedPoller(client)
    slow, fast = poller.poll_once()

    poller.acknowledge(fast)
    # `since` is inclusive, so stopping *at* 100 still re-fetches `slow`.
    assert poller._since == 100

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


def test_restart_replays_only_the_unfinished_turn_of_an_out_of_order_batch() -> None:
    """The out-of-order case: a newer completion must survive a restart on its own.

    The watermark is pinned to the older unfinished turn, so the newer one is
    recorded nowhere but the handled map — losing it would re-run a turn whose
    tool side effects already landed.
    """
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(
        _event("slow", created_at=100), _event("fast", created_at=200)
    )
    poller = BuzzFeedPoller(client)
    _slow, fast = poller.poll_once()
    poller.acknowledge(fast)  # newer turn done, older still running — then crash

    restarted = BuzzFeedPoller(client)

    assert [e.event_id for e in restarted.poll_once()] == ["slow"]


def test_handled_ids_below_the_watermark_are_pruned() -> None:
    """Only IDs at or above `since` are kept, so the map cannot grow forever."""
    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(
        _event("old", created_at=100), _event("new", created_at=200)
    )
    poller = BuzzFeedPoller(client)
    old, new = poller.poll_once()

    poller.acknowledge(old)
    poller.acknowledge(new)

    assert poller._since == 200
    assert poller._handled == {"new": 200}


def test_a_corrupt_cursor_file_replays_rather_than_skips() -> None:
    from gateway.transports.buzz.poller import cursor

    cursor.BUZZ_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    cursor.BUZZ_CURSOR_FILE.write_text("{not json")

    client = MagicMock(spec=BuzzClient)
    client.get_feed.return_value = _feed(_event("ev1", created_at=100))

    assert [e.event_id for e in BuzzFeedPoller(client).poll_once()] == ["ev1"]


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
