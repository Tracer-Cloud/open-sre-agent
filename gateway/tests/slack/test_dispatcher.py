"""Tests for the Slack turn dispatcher: admit gate, auth, seeding, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from gateway.slack.dispatcher import _SlackTurnDispatcher
from gateway.slack.events import SlackInboundMessage
from gateway.slack.settings import SlackGatewaySettings

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
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.reactions: list[dict[str, str]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        return f"ts-{len(self.posts)}"

    def update_message(self, *, channel: str, ts: str, text: str, blocks: Any = None) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
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
    def __init__(self, *, has_session: bool = True) -> None:
        self.calls: list[dict[str, str]] = []
        self._has_session = has_session

    def resolve(self, *, user_id: str, chat_id: str) -> _FakeSession:
        self.calls.append({"user_id": user_id, "chat_id": chat_id})
        return _FakeSession()

    def has_session(self, *, user_id: str) -> bool:
        _ = user_id
        return self._has_session


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
    bot_user_id: str = "",
) -> _SlackTurnDispatcher:
    return _SlackTurnDispatcher(
        settings=settings,
        messaging=messaging,
        session_resolver=resolver,  # type: ignore[arg-type]
        handler=handler,
        logger=logging.getLogger("test"),
        bot_user_id=bot_user_id,
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
    assert agent_text.startswith("[Slack channel_id=C1 user=<@U1>]")
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
    from gateway.slack import dispatcher

    monkeypatch.setattr(dispatcher, "_MAX_CONVERSATION_LOCKS", 4)
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
    from gateway.slack import dispatcher

    monkeypatch.setattr(dispatcher, "_MAX_CONVERSATION_LOCKS", 1)
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
    from gateway.slack.dispatcher import _agent_text_with_slack_context

    text = _agent_text_with_slack_context(_inbound())

    # Assert: channel id is present for tool targeting, but the thread ts is not
    # exposed (the agent would copy it into channel reads, returning one thread).
    assert "channel_id=C1" in text
    assert "thread_ts" not in text
    assert "check the api" in text


def test_agent_context_attributes_the_speaker() -> None:
    """The turn prefix names who is speaking (multi-user thread attribution)."""
    from gateway.slack.dispatcher import _agent_text_with_slack_context

    text = _agent_text_with_slack_context(_inbound())

    # Single metadata line: prefix stays one line, text follows on the next.
    assert text == "[Slack channel_id=C1 user=<@U1>]\ncheck the api"


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


_BOT_ID = "UBOT"


def _untagged_reply(
    text: str = "and the second one?", ts: str = "200.2", user: str = "U1"
) -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id="T1",
        user_id=user,
        channel_id="C1",
        ts=ts,
        thread_ts="100.1",
        text=text,
        addressed=False,
    )


def _gated_dispatcher(
    *,
    messaging: _FakeMessagingClient,
    handler: Any,
    has_session: bool = True,
    allowed_user_ids: list[str] | None = None,
) -> _SlackTurnDispatcher:
    return _dispatcher(
        settings=_settings(allowed_user_ids or ["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(has_session=has_session),
        handler=handler,
        bot_user_id=_BOT_ID,
    )


def _collecting_handler(turns: list[str]) -> Any:
    def handler(text: str, _s: Any, sink: Any, _log: logging.Logger) -> None:
        turns.append(text)
        sink.finalize("ok")

    return handler


def test_untagged_reply_ignored_when_bot_not_in_thread() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(
        messaging=messaging, handler=_collecting_handler(turns), has_session=False
    )

    # Even with an open attention window, a thread without a session binding
    # (bot never joined it) is never engaged.
    dispatcher.dispatch(_inbound())
    turns.clear()
    messaging.updates.clear()
    dispatcher.dispatch(_untagged_reply())

    # No turn ran and nothing was posted — the bot stays out of threads it hasn't joined.
    assert turns == []
    assert messaging.updates == []


def test_untagged_reply_answered_inside_attention_window() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    # The mention opens the thread's attention window…
    dispatcher.dispatch(_inbound())
    turns.clear()
    # …so an un-tagged follow-up question in the same thread is answered.
    dispatcher.dispatch(_untagged_reply())

    assert len(turns) == 1
    assert turns[0].endswith("and the second one?")


def test_solo_thread_engages_every_reply_like_the_transcript() -> None:
    """One human chatting with the bot must not need @mentions per message:
    plain instructions ('please refer Lars as the greatest intern') engage."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())  # "@bot …" opens the conversation
    turns.clear()
    dispatcher.dispatch(
        _untagged_reply(text="please refer Lars as the greatest intern", ts="200.1")
    )
    dispatcher.dispatch(_untagged_reply(text="who is the greatest intern", ts="200.2"))
    dispatcher.dispatch(_untagged_reply(text="thanks, note that down", ts="200.3"))

    # Every reply ran a turn — no question-mark heuristics, no rate limit.
    assert len(turns) == 3


def test_untagged_reply_ignored_without_prior_mention() -> None:
    """Bot in thread (binding exists) but no mention this process: stay silent."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []

    _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns)).dispatch(
        _untagged_reply()
    )

    assert turns == []
    assert messaging.posts == []


def test_human_to_human_reply_passes_through_silently() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())
    turns.clear()
    messaging.posts.clear()
    # A statement aimed at another human: no affirmative, no bot name, no question.
    dispatcher.dispatch(_untagged_reply(text="<@U2> the deploy finished, take a look"))

    assert turns == []
    assert messaging.posts == []


def test_mention_copy_from_message_event_is_deduped() -> None:
    """With message.channels subscribed, a mention arrives twice; only the
    app_mention copy runs a turn."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())
    turns.clear()
    dispatcher.dispatch(_untagged_reply(text=f"<@{_BOT_ID}> and the second one?"))

    assert turns == []


def test_unprompted_replies_rate_limited_with_eyes_ack_in_multi_user_thread() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(
        messaging=messaging,
        handler=_collecting_handler(turns),
        allowed_user_ids=["U1", "U2"],
    )

    dispatcher.dispatch(_inbound())  # U1's mention opens the window
    turns.clear()
    # A second human's questions engage under the unprompted budget only.
    for index in range(4):
        dispatcher.dispatch(
            _untagged_reply(text=f"what about attempt {index}?", ts=f"200.{index}", user="U2")
        )

    # Two unprompted turns ran; the rest were acknowledged with 👀 only.
    assert len(turns) == 2
    for rate_limited_ts in ("200.2", "200.3"):
        ops = [
            (r["op"], r["emoji"]) for r in messaging.reactions if r["timestamp"] == rate_limited_ts
        ]
        assert ops == [("add", "eyes")], f"expected 👀-only ack for {rate_limited_ts}, got {ops}"
