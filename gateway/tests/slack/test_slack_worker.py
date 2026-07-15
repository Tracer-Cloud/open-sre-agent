from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from gateway.slack.events import SlackInboundMessage
from gateway.slack.settings import SlackGatewaySettings
from gateway.slack.socket_mode_worker import _SlackTurnDispatcher

_SECURITY = "gateway.slack.security"


@pytest.fixture(autouse=True)
def _isolate_slack_integration_store():
    """Worker tests must not depend on the developer's ~/.opensre integrations."""
    with (
        patch(f"{_SECURITY}.get_integration", return_value=None),
        patch(f"{_SECURITY}.upsert_instance"),
    ):
        yield


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, str | None]] = []
        self.updates: list[dict[str, str]] = []
        self.reactions: list[dict[str, str]] = []

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return f"ts-{len(self.posts)}"

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        self.reactions.append(
            {"op": "add", "channel": channel, "timestamp": timestamp, "emoji": emoji}
        )
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        self.reactions.append(
            {"op": "remove", "channel": channel, "timestamp": timestamp, "emoji": emoji}
        )
        return True


class _FakeSession:
    session_id = "session-12345678"


class _FakeSessionResolver:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def resolve(self, *, user_id: str, chat_id: str) -> _FakeSession:
        self.calls.append({"user_id": user_id, "chat_id": chat_id})
        return _FakeSession()


def _settings(
    allowed_user_ids: list[str] | None = None,
    *,
    allow_open_workspace: bool = False,
    turn_timeout_seconds: float = 240.0,
) -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        allowed_user_ids=allowed_user_ids or [],
        allow_open_workspace=allow_open_workspace,
        status_update_interval_seconds=0.01,
        turn_timeout_seconds=turn_timeout_seconds,
    )


def _inbound() -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id="T1",
        user_id="U1",
        channel_id="C1",
        ts="100.1",
        thread_ts="100.1",
        text="check the api",
    )


def _dispatcher(
    *,
    settings: SlackGatewaySettings,
    messaging: _FakeMessagingClient,
    resolver: _FakeSessionResolver,
    handler: Any,
) -> _SlackTurnDispatcher:
    return _SlackTurnDispatcher(
        settings=settings,
        messaging=messaging,
        session_resolver=resolver,  # type: ignore[arg-type]
        handler=handler,
        logger=logging.getLogger("test"),
    )


def test_authorized_message_reaches_handler_with_thread_sink() -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[tuple[str, Any]] = []

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        turns.append((text, session))
        sink.finalize("done")

    _dispatcher(
        settings=_settings(["U1"]), messaging=messaging, resolver=resolver, handler=handler
    ).dispatch(_inbound())

    assert len(turns) == 1
    agent_text, session = turns[0]
    assert agent_text.startswith("[Slack channel_id=C1]")
    assert "thread_ts" not in agent_text
    assert "slack_read_messages" not in agent_text
    assert agent_text.endswith("check the api")
    assert session is turns[0][1]
    assert resolver.calls == [{"user_id": "T1:C1:100.1", "chat_id": "C1"}]
    # Placeholder posted into the thread, then edited with the final answer.
    assert messaging.posts[0]["thread_ts"] == "100.1"
    assert messaging.updates[-1]["text"] == "done"
    # Viktor-like coworker UX: eyes while working, then checkmark.
    emoji_ops = [(r["op"], r["emoji"]) for r in messaging.reactions]
    assert ("add", "eyes") in emoji_ops
    assert ("remove", "eyes") in emoji_ops
    assert ("add", "white_check_mark") in emoji_ops


def test_unauthorized_user_gets_denial_reply_and_no_turn() -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[str] = []

    _dispatcher(
        settings=_settings(["U999"]),
        messaging=messaging,
        resolver=resolver,
        handler=lambda text, *_args: turns.append(text),
    ).dispatch(_inbound())

    assert turns == []
    assert resolver.calls == []
    # Generic reply only — no user ids, allowlists, or env var names leak to the channel.
    denial = messaging.posts[0]["text"] or ""
    assert "not authorized" in denial
    assert "U1" not in denial
    assert "SLACK_" not in denial


def test_conversation_locks_are_pruned_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.slack import socket_mode_worker

    monkeypatch.setattr(socket_mode_worker, "_MAX_CONVERSATION_LOCKS", 4)
    dispatcher = _dispatcher(
        settings=_settings(["U1"]),
        messaging=_FakeMessagingClient(),
        resolver=_FakeSessionResolver(),
        handler=lambda *_args: None,
    )

    for index in range(10):
        with dispatcher._conversation_turn(f"T1:C1:{index}"):
            pass

    assert len(dispatcher._conversation_locks) <= 4 + 1


def test_in_use_conversation_lock_survives_pruning(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.slack import socket_mode_worker

    monkeypatch.setattr(socket_mode_worker, "_MAX_CONVERSATION_LOCKS", 1)
    dispatcher = _dispatcher(
        settings=_settings(["U1"]),
        messaging=_FakeMessagingClient(),
        resolver=_FakeSessionResolver(),
        handler=lambda *_args: None,
    )

    with dispatcher._conversation_turn("T1:C1:busy"):
        busy_entry = dispatcher._conversation_locks["T1:C1:busy"]
        # Another conversation triggers pruning while the first turn is running.
        with dispatcher._conversation_turn("T1:C1:other"):
            pass
        # The in-use entry was never discarded or replaced.
        assert dispatcher._conversation_locks["T1:C1:busy"] is busy_entry


def test_handler_exception_is_contained() -> None:
    messaging = _FakeMessagingClient()

    def handler(*_args: Any) -> None:
        raise RuntimeError("boom")

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=handler,
    ).dispatch(_inbound())


def test_errored_turn_replaces_placeholder_with_error() -> None:
    """A raising handler must leave a visible error in the thread, not a frozen
    'Digging in…' placeholder (only the reaction changing)."""
    messaging = _FakeMessagingClient()

    def handler(_text: str, _session: Any, _sink: Any, _logger: logging.Logger) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _dispatcher(
            settings=_settings(["U1"]),
            messaging=messaging,
            resolver=_FakeSessionResolver(),
            handler=handler,
        )._run_turn(_inbound())

    # The placeholder message was edited to an error, and the message shows ✗.
    assert messaging.updates, "placeholder was never updated on error"
    assert "went wrong" in messaging.updates[-1]["text"].lower()
    assert ("add", "x") in [(r["op"], r["emoji"]) for r in messaging.reactions]


def test_agent_context_omits_thread_ts_to_avoid_thread_reads() -> None:
    # Arrange / Act
    from gateway.slack.socket_mode_worker import _agent_text_with_slack_context

    text = _agent_text_with_slack_context(_inbound())

    # Assert: channel id is present for tool targeting, but the thread ts is not
    # exposed (the agent would copy it into channel reads, returning one thread).
    assert "channel_id=C1" in text
    assert "thread_ts" not in text
    assert "check the api" in text


def test_turn_timeout_finalizes_placeholder_when_handler_hangs() -> None:
    """A turn that outruns the timeout gets a visible message + ✗ instead of a
    frozen placeholder, even though the blocking handler cannot be cancelled."""
    messaging = _FakeMessagingClient()
    release = threading.Event()

    def hanging_handler(_text: str, _session: Any, _sink: Any, _logger: logging.Logger) -> None:
        release.wait(5.0)  # blocks past the tiny timeout; released once observed

    dispatcher = _dispatcher(
        settings=_settings(["U1"], turn_timeout_seconds=0.05),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=hanging_handler,
    )
    worker = threading.Thread(target=lambda: dispatcher._run_turn(_inbound()))
    worker.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
            "taking longer" in update["text"].lower() for update in messaging.updates
        ):
            time.sleep(0.02)
    finally:
        release.set()
        worker.join(5.0)

    assert any("taking longer" in update["text"].lower() for update in messaging.updates), (
        "timeout did not replace the placeholder"
    )
    ops = [(r["op"], r["emoji"]) for r in messaging.reactions]
    assert ("add", "x") in ops
    # The timeout owns the outcome, so a late normal completion must not stack a
    # done tick over the timeout's cross.
    assert ("add", "white_check_mark") not in ops
