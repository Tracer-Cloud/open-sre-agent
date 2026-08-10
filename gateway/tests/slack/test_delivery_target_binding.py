"""The Slack turn must bind the delivery target a detached investigation posts back to.

A detached investigation outlives the turn that launched it, so the only way it can
reach the right thread is the delivery-target ContextVar the dispatcher binds around
the handler. Two things have to hold, and neither is provable from the launcher side:

* the dispatcher binds the *real* inbound coordinates (not a placeholder), and resets
  them afterwards so a pooled worker thread cannot leak one turn's thread into the next;
* the Slack transport registers its notifier under the *same* platform key the
  dispatcher stamps on the target — a mismatch makes every registry lookup miss and
  silently drops the ack and the report.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from config.constants import PLATFORM_SLACK, paths
from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from config.principal import Principal
from core.agent_harness.session import InMemorySessionStorage, SessionCore, SessionManager
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.chat import get_chat_notifier_registry
from gateway.core.chat.delivery_context import get_current_delivery_target
from gateway.core.chat.delivery_target import ChatDeliveryTarget
from gateway.core.storage import FileBindingStore, SessionResolver
from gateway.transports.slack.dispatcher import _SlackTurnDispatcher
from gateway.transports.slack.events import SlackInboundMessage
from gateway.transports.slack.principal import slack_scope
from gateway.transports.slack.security import SlackInboundDecision
from gateway.transports.slack.settings import SlackGatewaySettings

_ORG = "org_delivery_binding"
_TEAM = "T_BIND"
_CHANNEL = "C_BIND"
_USER = "U_BIND"
_THREAD = "1700000000.000100"
#: A reply's own timestamp, distinct from the thread it replies into — the
#: shape that tells ``origin_message_id`` (must track ``ts``) apart from
#: ``thread_ts`` (the thread's first message).
_REPLY_TS = "1700000000.000105"


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.reactions: list[dict[str, Any]] = []

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


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, _ORG)
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionResolver:
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)
    store = FileBindingStore(tmp_path / "bindings.json")
    repo = SimpleNamespace(load_session=lambda _session_id: None)
    manager = SessionManager(storage=InMemorySessionStorage(), repo=repo)
    return SessionResolver(store, manager=manager, platform="slack")


@pytest.fixture
def allow_all_security() -> Any:
    decision = SlackInboundDecision(allowed=True)
    with (
        patch(
            "gateway.transports.slack.dispatcher.enforce_inbound_slack_message_security",
            return_value=decision,
        ),
        patch("gateway.transports.slack.dispatcher.persist_policy_if_needed"),
        patch("gateway.transports.slack.dispatcher.session_needs_thread_seed", return_value=False),
        patch(
            "gateway.transports.slack.dispatcher.consume_credits",
            return_value=CreditsOutcome.UNCONFIGURED,
        ),
        patch("gateway.transports.slack.dispatcher.mark_turn_working"),
        patch("gateway.transports.slack.dispatcher.mark_turn_done"),
        patch("gateway.transports.slack.dispatcher.mark_turn_failed"),
    ):
        yield


def _settings() -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        allowed_user_ids=[],
        allow_open_workspace=True,
        status_update_interval_seconds=0.01,
        turn_timeout_seconds=60.0,
    )


def _inbound(*, thread_ts: str = _THREAD, ts: str | None = None) -> SlackInboundMessage:
    """``ts`` defaults to ``thread_ts`` (a fresh mention); pass it explicitly for a reply."""
    return SlackInboundMessage(
        team_id=_TEAM,
        user_id=_USER,
        channel_id=_CHANNEL,
        ts=ts if ts is not None else thread_ts,
        thread_ts=thread_ts,
        text="hello",
    )


@pytest.mark.usefixtures("allow_all_security")
def test_turn_binds_the_inbound_thread_as_the_delivery_target(resolver: SessionResolver) -> None:
    """The bound target must carry the real channel/thread, not a placeholder.

    Reverting the ``bound_delivery_target(...)`` entry in the dispatcher's ``with``
    leaves the target unbound, so every chat investigation would fall back to the
    local path and never post anything back to Slack.
    """
    seen: list[ChatDeliveryTarget | None] = []

    def handler(_text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        seen.append(get_current_delivery_target())
        sink.finalize("ok")

    dispatcher = _SlackTurnDispatcher(
        settings=_settings(),
        messaging=_FakeMessagingClient(),
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.delivery.binding"),
    )
    # A reply inside an existing thread: ``ts`` (this message) differs from
    # ``thread_ts`` (the thread's first message) — origin_message_id must track
    # the former, since that is the message a completion reaction lands on.
    dispatcher._run_turn(
        _inbound(thread_ts=_THREAD, ts=_REPLY_TS), slack_scope(Principal.org(_ORG), _USER)
    )

    assert len(seen) == 1
    target = seen[0]
    assert target is not None, "the turn ran with no delivery target bound"
    assert target.platform == PLATFORM_SLACK
    assert target.channel_id == _CHANNEL
    assert target.thread_ts == _THREAD
    assert target.user_id == _USER
    assert target.origin_message_id == _REPLY_TS
    assert target.origin_message_id != target.thread_ts


@pytest.mark.usefixtures("allow_all_security")
def test_delivery_target_does_not_leak_to_the_next_turn(resolver: SessionResolver) -> None:
    """Socket Mode reuses pool threads, so an unreset target would post into a stale thread."""
    seen: list[ChatDeliveryTarget | None] = []

    def handler(_text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        seen.append(get_current_delivery_target())
        sink.finalize("ok")

    dispatcher = _SlackTurnDispatcher(
        settings=_settings(),
        messaging=_FakeMessagingClient(),
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.delivery.leak"),
    )

    scope = slack_scope(Principal.org(_ORG), _USER)

    # One worker thread, two turns in different Slack threads — the second must not
    # observe the first one's target, and nothing may survive after the pool goes idle.
    def _both() -> None:
        dispatcher._run_turn(_inbound(thread_ts="1700000000.000111"), scope)
        dispatcher._run_turn(_inbound(thread_ts="1700000000.000222"), scope)

    worker = threading.Thread(target=_both, name="slack-pool-worker")
    worker.start()
    worker.join(timeout=30.0)
    assert not worker.is_alive()

    assert [t.thread_ts for t in seen if t is not None] == [
        "1700000000.000111",
        "1700000000.000222",
    ]
    assert get_current_delivery_target() is None


def test_slack_transport_registers_under_the_key_the_turn_binds() -> None:
    """Registration key and delivery-target platform must be the same string.

    They are set in different packages. If they drift, ``registry.get(...)`` misses,
    the launcher takes its "no notifier" branch, and the ack and the final report are
    both dropped with nothing louder than a log line.
    """
    from gateway.transports.slack.chat_notifier import register_slack_notifier

    registry = get_chat_notifier_registry()
    previous = registry.get(PLATFORM_SLACK)
    try:
        register_slack_notifier(SimpleNamespace())
        target = ChatDeliveryTarget(
            platform=PLATFORM_SLACK,
            channel_id=_CHANNEL,
            thread_ts=_THREAD,
            user_id=_USER,
        )
        assert registry.get(target.platform) is not None
    finally:
        if previous is not None:
            registry.register(PLATFORM_SLACK, previous)
