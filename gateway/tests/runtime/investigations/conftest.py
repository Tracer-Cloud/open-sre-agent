"""Shared fakes for the detached chat-investigation suite.

Real objects wherever one is cheap: ``InMemoryInvestigationStore`` is the store
the no-database path actually uses, and ``SlackChatNotifier`` is the only
notifier that ships. Only the two process boundaries are faked — the Slack web
client and the investigation pipeline — because those are what a test cannot
run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from config.constants import PLATFORM_SLACK, datastore
from gateway.core.chat import (
    ChatDeliveryTarget,
    ChatNotifier,
    DetachedInvestigationAck,
    get_chat_notifier_registry,
    reset_chat_notifier_registry_for_tests,
)
from gateway.core.investigations import detached_launcher
from gateway.core.investigations.storage_utils import reset_investigation_store_for_tests
from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationOrigin,
    InvestigationRecord,
)
from gateway.transports.slack.chat_notifier import SlackChatNotifier


class FakeSlackClient:
    """Records what would have gone to Slack, and can be told to fail.

    ``post_message`` returns a fresh ``ts`` per call so a test can tell an edit
    of the acknowledgment from a second post — the distinction the one-message
    stage-update contract rests on.
    """

    def __init__(self, *, post_succeeds: bool = True, update_succeeds: bool = True) -> None:
        self.posted: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.reactions: list[dict[str, str]] = []
        self._post_succeeds = post_succeeds
        self._update_succeeds = update_succeeds
        self._next_ts = 0

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posted.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        if not self._post_succeeds:
            return None
        self._next_ts += 1
        return f"170000000.{self._next_ts:06d}"

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: Any = None,
    ) -> bool:
        self.updated.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return self._update_succeeds

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

    def delete_message(self, *, channel: str, ts: str) -> bool:
        _ = (channel, ts)
        return True


class RecordingNotifier:
    """A ``ChatNotifier`` that records calls instead of reaching a platform."""

    def __init__(self, *, delivers: bool = True) -> None:
        self.acks: list[DetachedInvestigationAck] = []
        self.stages: list[str] = []
        self.finals: list[str] = []
        self.failures: list[str] = []
        self.origin_completed: list[ChatDeliveryTarget] = []
        self.origin_failed: list[ChatDeliveryTarget] = []
        self._delivers = delivers

    def post_ack(self, target: ChatDeliveryTarget, ack: DetachedInvestigationAck) -> str | None:
        _ = target
        self.acks.append(ack)
        return "170000000.000001"

    def update_stage(self, target: ChatDeliveryTarget, stage: str, investigation_id: str) -> None:
        _ = (target, investigation_id)
        self.stages.append(stage)

    def deliver_final(self, target: ChatDeliveryTarget, report: str, investigation_id: str) -> bool:
        _ = (target, investigation_id)
        self.finals.append(report)
        return self._delivers

    def report_failure(
        self, target: ChatDeliveryTarget, error_summary: str, investigation_id: str
    ) -> None:
        _ = (target, investigation_id)
        self.failures.append(error_summary)

    def mark_origin_complete(self, target: ChatDeliveryTarget) -> None:
        self.origin_completed.append(target)

    def mark_origin_failed(self, target: ChatDeliveryTarget) -> None:
        self.origin_failed.append(target)


@pytest.fixture(autouse=True)
def _clean_notifier_registry() -> Iterator[None]:
    """Isolate every test from notifiers registered by another.

    Autouse and both-sided: the registry is a process global, so a leak here
    makes a later "capability withheld" assertion pass for the wrong reason.
    """
    reset_chat_notifier_registry_for_tests()
    yield
    reset_chat_notifier_registry_for_tests()


@pytest.fixture(autouse=True)
def _no_real_pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Stop a launch from starting a real investigation thread.

    Autouse rather than opt-in on purpose. The no-database path launches on a
    daemon thread whose default runner is ``AgentSession().investigate`` — a test
    that forgets to patch it makes live LLM and vendor calls, and does so in the
    background where the failure surfaces as an unrelated flake.
    """
    launched: list[str] = []

    def _record(store: Any, investigation_id: str, target: ChatDeliveryTarget) -> None:
        _ = (store, target)
        launched.append(investigation_id)

    monkeypatch.setattr(detached_launcher, "_run_investigation_background", _record)
    # A DATABASE_URI in the developer's environment would otherwise route the
    # launch to the process-wide worker thread instead, which is just as live.
    monkeypatch.setattr(datastore, "database_dsn", lambda: None)
    reset_investigation_store_for_tests()
    yield launched
    reset_investigation_store_for_tests()


@pytest.fixture
def delivery_target() -> ChatDeliveryTarget:
    """A Slack thread to deliver into."""
    return ChatDeliveryTarget(
        platform=PLATFORM_SLACK,
        channel_id="C_TEST",
        thread_ts="170000000.000000",
        user_id="U_TEST",
    )


@pytest.fixture
def delivery_target_with_origin() -> ChatDeliveryTarget:
    """A Slack thread to deliver into, with the triggering message identified.

    ``origin_message_id`` is what a detached run's completion reaction lands
    on — distinct from ``thread_ts``, which can be an earlier message in a
    long-running thread.
    """
    return ChatDeliveryTarget(
        platform=PLATFORM_SLACK,
        channel_id="C_TEST",
        thread_ts="170000000.000000",
        user_id="U_TEST",
        origin_message_id="170000000.000042",
    )


@pytest.fixture
def notifier() -> RecordingNotifier:
    """A notifier that succeeds and remembers everything it was asked to send."""
    return RecordingNotifier()


@pytest.fixture
def register_notifier() -> Callable[[ChatNotifier], None]:
    """Register a notifier for Slack; the autouse fixture undoes it."""

    def _register(chat_notifier: ChatNotifier) -> None:
        get_chat_notifier_registry().register(PLATFORM_SLACK, chat_notifier)

    return _register


@pytest.fixture
def slack_client() -> FakeSlackClient:
    """A stand-in for the Slack web client."""
    return FakeSlackClient()


@pytest.fixture
def slack_notifier(slack_client: FakeSlackClient) -> SlackChatNotifier:
    """The real Slack notifier over a fake client."""
    return SlackChatNotifier(slack_client=slack_client)


@pytest.fixture
def store() -> InMemoryInvestigationStore:
    """The store the no-database path actually uses."""
    return InMemoryInvestigationStore()


@pytest.fixture
def make_record(
    store: InMemoryInvestigationStore, delivery_target: ChatDeliveryTarget
) -> Callable[..., InvestigationRecord]:
    """Build a chat-origin record carrying a delivery target and a storage scope."""

    def _make(
        *,
        org_id: str | None = "org-test",
        actor_id: str = "U_TEST",
        include_delivery_target: bool = True,
    ) -> InvestigationRecord:
        trigger: dict[str, Any] = {
            "raw_alert": {"alert_text": "checkout latency is up"},
            "alert_name": "chat:checkout latency is up",
        }
        if include_delivery_target:
            trigger["delivery_target"] = {
                "platform": delivery_target.platform,
                "channel_id": delivery_target.channel_id,
                "thread_ts": delivery_target.thread_ts,
                "user_id": delivery_target.user_id,
            }
        if org_id is not None:
            trigger["scope"] = {"org_id": org_id, "actor_id": actor_id}
        return store.create(
            clerk_org_id=org_id or "chat",
            trigger=trigger,
            origin=InvestigationOrigin.CHAT,
        )

    return _make
