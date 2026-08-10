"""The message that asked gets its own completion signal (Defect B).

Fixing the action tool so it actually launches a detached run (Defect A) means
the turn's ✅/👀 can no longer stand for "done" — the run may still be in
flight after the turn ends. The origin message itself has to carry that
outcome once the detached run actually finishes.
"""

from __future__ import annotations

from typing import Any

from config.constants import SLACK_REACTION_DONE, SLACK_REACTION_FAILED, SLACK_REACTION_WORKING
from gateway.core.investigations.chat_worker import ChatInvestigationWorker
from gateway.core.storage.investigations.store import InvestigationStatus

from .conftest import FakeSlackClient, RecordingNotifier


def _final_reactions(client: FakeSlackClient, *, channel: str, timestamp: str) -> set[str]:
    """Fold ``client.reactions`` into the emoji set currently present on one message."""
    present: set[str] = set()
    for op in client.reactions:
        if op["channel"] != channel or op["timestamp"] != timestamp:
            continue
        if op["op"] == "add":
            present.add(op["emoji"])
        elif op["op"] == "remove":
            present.discard(op["emoji"])
    return present


def _quiet_runner(trigger: dict[str, Any]) -> dict[str, Any]:
    _ = trigger
    return {"report": "root cause: a bad deploy"}


def test_worker_marks_origin_message_on_completion(
    store, make_record, notifier, register_notifier, tmp_path
):
    """A delivered report must also flip the origin message's reaction."""
    register_notifier(notifier)
    record = make_record()

    ChatInvestigationWorker(
        store, runner=_quiet_runner, artifacts_dir=tmp_path
    )._process_investigation(record)

    assert len(notifier.origin_completed) == 1
    assert notifier.origin_failed == []
    assert store.get(record.id).status is InvestigationStatus.COMPLETED


def test_failed_delivery_marks_origin_failed_not_done(
    store, make_record, register_notifier, tmp_path
):
    """A report that never reached the thread must not stamp a false ✅.

    Calling ``mark_origin_complete`` unconditionally here would tell the user
    an investigation succeeded when nothing was ever delivered.
    """
    undelivering = RecordingNotifier(delivers=False)
    register_notifier(undelivering)
    record = make_record()

    ChatInvestigationWorker(
        store, runner=_quiet_runner, artifacts_dir=tmp_path
    )._process_investigation(record)

    assert undelivering.origin_completed == []
    assert len(undelivering.origin_failed) == 1
    assert store.get(record.id).status is InvestigationStatus.FAILED


def test_mark_origin_complete_clears_stale_working_and_failed_reactions(
    slack_notifier, slack_client, delivery_target_with_origin
):
    """A detached success must not leave a stale 👀 or ✗ beside the ✅.

    A turn can stamp both "working" and "failed" on the origin message before
    its detached run later succeeds (timeout, user-stop, or an earlier error
    that the run recovered from). ``mark_detached_done`` is supposed to clear
    *both* before adding the checkmark — this drives the real ``SlackChatNotifier``
    over a fake client pre-seeded with both stale reactions, using the
    ``delivery_target_with_origin`` fixture built for exactly this and never
    otherwise referenced.
    """
    target = delivery_target_with_origin
    slack_client.add_reaction(
        channel=target.channel_id,
        timestamp=target.origin_message_id,
        emoji=SLACK_REACTION_WORKING,
    )
    slack_client.add_reaction(
        channel=target.channel_id,
        timestamp=target.origin_message_id,
        emoji=SLACK_REACTION_FAILED,
    )

    slack_notifier.mark_origin_complete(target)

    assert _final_reactions(
        slack_client, channel=target.channel_id, timestamp=target.origin_message_id
    ) == {SLACK_REACTION_DONE}


def test_unbound_scope_marks_origin_failed(
    store, make_record, notifier, register_notifier, tmp_path
):
    """A record with no scope data must fail closed and tell the origin thread.

    ``StorageScope`` cannot be reconstructed without an org id, and running the
    investigation unscoped would resolve integrations for the wrong tenant. The
    early ``unbound_scope`` return must not leave the origin message stuck at
    👀 forever — it has to stamp the same failure signal any other failure path
    does.
    """
    register_notifier(notifier)
    record = make_record(org_id=None)

    ChatInvestigationWorker(
        store, runner=_quiet_runner, artifacts_dir=tmp_path
    )._process_investigation(record)

    assert len(notifier.origin_failed) == 1
    assert notifier.origin_completed == []
    assert store.get(record.id).status is InvestigationStatus.FAILED


def test_trigger_without_origin_message_id_is_a_no_op(
    slack_notifier, slack_client, delivery_target
):
    """A record written before this field existed must not crash or react.

    ``trigger`` is unversioned JSONB — a pre-existing row has no
    ``origin_message_id`` at all, and ``ChatDeliveryTarget`` defaults it to
    ``None``. The real notifier must treat that as "nowhere to react", not
    forward ``None`` as a timestamp.
    """
    assert delivery_target.origin_message_id is None

    slack_notifier.mark_origin_complete(delivery_target)
    slack_notifier.mark_origin_failed(delivery_target)

    assert slack_client.reactions == []
