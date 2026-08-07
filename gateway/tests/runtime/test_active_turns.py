"""Active-turn cancel registry and /stop command parsing."""

from __future__ import annotations

import threading

from gateway.core.runtime.active_turns import (
    ActiveTurnCancels,
    is_stop_command,
)


def test_is_stop_command_accepts_common_forms() -> None:
    assert is_stop_command("/stop")
    assert is_stop_command("stop")
    assert is_stop_command("/cancel")
    assert is_stop_command("  /stop@OpenSREBot please ")
    assert not is_stop_command("/status")
    assert not is_stop_command("please stop later")
    assert not is_stop_command("")


def test_request_stop_sets_event_and_runs_callback() -> None:
    registry = ActiveTurnCancels()
    event = threading.Event()
    seen: list[str] = []

    with registry.track("chat-1", event, on_user_stop=lambda: seen.append("stop")):
        assert registry.request_stop("chat-1") is True
        assert event.is_set()
        assert seen == ["stop"]

    assert registry.request_stop("chat-1") is False


def test_track_unregisters_even_when_turn_raises() -> None:
    registry = ActiveTurnCancels()
    event = threading.Event()
    try:
        with registry.track("chat-1", event):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert registry.request_stop("chat-1") is False


def test_newer_track_replaces_key() -> None:
    registry = ActiveTurnCancels()
    first = threading.Event()
    second = threading.Event()
    with registry.track("chat-1", first), registry.track("chat-1", second):
        assert registry.request_stop("chat-1") is True
        assert second.is_set()
        assert not first.is_set()
