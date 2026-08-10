"""What the worker owes the thread that asked: a report, stages, and honest status."""

from __future__ import annotations

from typing import Any

import pytest

from gateway.core.chat import ChatDeliveryTarget
from gateway.core.investigations.chat_worker import (
    ChatInvestigationWorker,
    run_investigation_in_process,
)
from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationOrigin,
    InvestigationStatus,
)
from gateway.transports.slack.chat_notifier import SlackChatNotifier
from gateway.transports.slack.output_sink import (
    SLACK_MAX_MESSAGE_CHARS,
    SLACK_MAX_TASK_UPDATE_CHARS,
)
from platform.observability.render.progress import get_progress_tracker

from .conftest import FakeSlackClient, RecordingNotifier


def _worker(
    store: InMemoryInvestigationStore,
    runner: Any,
    tmp_path: Any,
) -> ChatInvestigationWorker:
    """A worker wired to a fake pipeline instead of the real one."""
    return ChatInvestigationWorker(store, runner=runner, artifacts_dir=tmp_path)


def _reporting_runner(*stages: str) -> Any:
    """A fake pipeline that announces ``stages`` through the ambient tracker."""

    def _run(trigger: dict[str, Any]) -> dict[str, Any]:
        _ = trigger
        tracker = get_progress_tracker()
        for stage in stages:
            tracker.start(stage)
        return {"report": "root cause: a bad deploy"}

    return _run


def _quiet_runner(trigger: dict[str, Any]) -> dict[str, Any]:
    """A fake pipeline that reports nothing and succeeds."""
    _ = trigger
    return {"report": "root cause: a bad deploy"}


class TestReportDelivery:
    def test_report_reaches_the_originating_thread(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The report is delivered by the notifier, not returned to a dead turn."""
        register_notifier(notifier)
        record = make_record()

        _worker(store, _quiet_runner, tmp_path)._process_investigation(record)

        assert notifier.finals == ["root cause: a bad deploy"]
        assert store.get(record.id).status is InvestigationStatus.COMPLETED

    def test_undelivered_report_is_not_recorded_as_completed(
        self, store, make_record, register_notifier, tmp_path
    ):
        """A run whose report never reached the thread is a failure, not a success.

        Recording COMPLETED here is the worst outcome available: the reader sees
        nothing and the operator sees a green row, so nobody knows to look.
        """
        undelivering = RecordingNotifier(delivers=False)
        register_notifier(undelivering)
        record = make_record()

        _worker(store, _quiet_runner, tmp_path)._process_investigation(record)

        stored = store.get(record.id)
        assert stored.status is InvestigationStatus.FAILED
        assert stored.error == "delivery_failed"

    def test_pipeline_failure_reports_no_exception_detail(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Slack is an external surface: the cause is logged, never posted (CWE-209)."""

        def _exploding_runner(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            raise RuntimeError("postgres://user:hunter2@db.internal/prod is unreachable")

        register_notifier(notifier)
        record = make_record()

        _worker(store, _exploding_runner, tmp_path)._process_investigation(record)

        assert notifier.failures, "the thread was never told the run failed"
        posted = " ".join(notifier.failures)
        assert "hunter2" not in posted
        assert "db.internal" not in posted
        assert store.get(record.id).status is InvestigationStatus.FAILED


class TestStageUpdates:
    def test_stages_reach_the_thread_during_the_run(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The tracker installed for the run must actually post.

        Pins the whole point of the live-updates decision. Both ways this broke
        before were silent: the tracker had no investigation id to quote, and no
        delivery target was bound on the worker thread to look up.
        """
        register_notifier(notifier)
        record = make_record()
        runner = _reporting_runner("extract_alert", "investigation_agent")

        _worker(store, runner, tmp_path)._process_investigation(record)

        assert notifier.stages == ["Reading the alert", "Gathering evidence"]

    def test_a_repeated_stage_posts_once(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Stages restart within a run; the thread should not count the retries."""
        register_notifier(notifier)
        record = make_record()
        runner = _reporting_runner("investigation_agent", "investigation_agent")

        _worker(store, runner, tmp_path)._process_investigation(record)

        assert notifier.stages == ["Gathering evidence"]

    def test_tool_traffic_never_reaches_the_thread(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """An investigation runs dozens of tools per stage; none are chat-worthy."""

        def _tool_heavy_runner(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            tracker = get_progress_tracker()
            tracker.start("investigation_agent")
            for _index in range(40):
                tracker.record_tool_start("kubernetes_get_pod_logs")
                tracker.record_tool_end("kubernetes_get_pod_logs")
            return {"report": "done"}

        register_notifier(notifier)
        record = make_record()

        _worker(store, _tool_heavy_runner, tmp_path)._process_investigation(record)

        assert notifier.stages == ["Gathering evidence"]

    def test_an_unknown_stage_is_announced_generically(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The pipeline can add a node without anyone updating the label table.

        The reader gets ``Processing`` for it, which says nothing but says nothing
        confusing. Echoing the raw node name instead publishes an internal
        identifier into a customer channel and reads like a bug in the product.
        """
        register_notifier(notifier)
        record = make_record()
        runner = _reporting_runner("some_new_internal_node")

        _worker(store, runner, tmp_path)._process_investigation(record)

        assert notifier.stages == ["Processing"]

    def test_a_failing_stage_names_the_stage_and_not_the_cause(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Second CWE-209 surface, and the one that is easy to miss.

        ``ProgressReporter.error`` is handed the exception text. The terminal
        tracker prints it; this one posts to Slack, so it must publish the stage
        label alone. The run may still finish — the terminal outcome comes from the
        worker — so this says the *stage* failed, not the investigation.
        """

        def _stage_failing_runner(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            get_progress_tracker().error(
                "resolve_integrations",
                "postgres://user:hunter2@db.internal/prod is unreachable",
            )
            return {"report": "root cause: a bad deploy"}

        register_notifier(notifier)
        record = make_record()

        _worker(store, _stage_failing_runner, tmp_path)._process_investigation(record)

        assert notifier.stages == ["Connecting to your integrations — failed"]
        assert "hunter2" not in notifier.stages[0]
        assert "db.internal" not in notifier.stages[0]

    def test_the_tracker_is_uninstalled_after_the_run(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """A leaked override would send a later local run's stages to this thread."""
        register_notifier(notifier)
        record = make_record()

        _worker(store, _quiet_runner, tmp_path)._process_investigation(record)

        get_progress_tracker().start("investigation_agent")
        assert notifier.stages == []


class TestSlackNotifierShape:
    def test_stage_updates_edit_the_acknowledgment(
        self, slack_notifier, slack_client, delivery_target
    ):
        """Stages edit one message. Posting each one would bury the thread."""
        from gateway.core.chat import DetachedInvestigationAck

        ack = DetachedInvestigationAck(investigation_id="abc123def", message="queued")
        slack_notifier.post_ack(delivery_target, ack)

        slack_notifier.update_stage(delivery_target, "Reading the alert", "abc123def")
        slack_notifier.update_stage(delivery_target, "Gathering evidence", "abc123def")

        assert len(slack_client.posted) == 1
        assert len(slack_client.updated) == 2
        # Both edits target the acknowledgment, not a message each.
        assert {entry["ts"] for entry in slack_client.updated} == {"170000000.000001"}

    def test_stage_update_stays_within_the_task_update_cap(
        self, slack_notifier, slack_client, delivery_target
    ):
        """Slack rejects an over-long update, which would drop the stage silently."""
        slack_notifier.update_stage(delivery_target, "x" * 1000, "abc123def")

        assert len(slack_client.posted) == 1
        assert len(slack_client.posted[0]["text"]) <= SLACK_MAX_TASK_UPDATE_CHARS

    def test_oversized_report_is_clamped_rather_than_dropped(
        self, slack_notifier, slack_client, delivery_target
    ):
        """A 14 MB pod-log answer once got ``msg_too_long`` and reached nobody.

        Clamping keeps the head of the report — the root cause is written first —
        instead of losing the whole message to the transport.
        """
        delivered = slack_notifier.deliver_final(delivery_target, "x" * 200_000, "abc123def")

        assert delivered is True
        text = slack_client.posted[0]["text"]
        assert len(text) <= SLACK_MAX_MESSAGE_CHARS
        assert text.endswith("…")

    def test_delivery_failure_is_reported_as_false(self, delivery_target):
        """The worker's status depends on this bool; swallowing to True hides a drop."""
        failing = SlackChatNotifier(slack_client=FakeSlackClient(post_succeeds=False))

        assert failing.deliver_final(delivery_target, "report", "abc123def") is False

    def test_the_report_is_posted_into_the_thread(
        self, slack_notifier, slack_client, delivery_target
    ):
        """Dropping ``thread_ts`` posts the answer to the channel, out of context."""
        slack_notifier.deliver_final(delivery_target, "report", "abc123def")

        assert slack_client.posted[0]["thread_ts"] == delivery_target.thread_ts


class TestWorkerPreconditions:
    @pytest.mark.parametrize(
        ("mutate", "expected_error"),
        [
            (lambda trigger: trigger.pop("delivery_target"), "no_delivery_target"),
            (lambda trigger: trigger.__setitem__("delivery_target", {"bogus": 1}), None),
        ],
    )
    def test_a_record_it_cannot_deliver_never_runs(
        self, store, make_record, notifier, register_notifier, tmp_path, mutate, expected_error
    ):
        """Without a usable target the pipeline spend buys nothing."""
        register_notifier(notifier)
        record = make_record()
        mutate(record.trigger)

        def _must_not_run(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            raise AssertionError("the pipeline ran for an undeliverable record")

        _worker(store, _must_not_run, tmp_path)._process_investigation(record)

        stored = store.get(record.id)
        assert stored.status is InvestigationStatus.FAILED
        if expected_error is not None:
            assert stored.error == expected_error

    def test_an_unregistered_platform_never_runs(self, store, make_record, tmp_path):
        """No notifier registered means the report has nowhere to go."""
        record = make_record()

        def _must_not_run(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            raise AssertionError("the pipeline ran with no notifier registered")

        _worker(store, _must_not_run, tmp_path)._process_investigation(record)

        assert store.get(record.id).error == "no_notifier"


class TestInProcessFallback:
    """The no-database path, which runs the record it just created itself."""

    def test_a_record_is_investigated_once_even_if_the_run_is_asked_for_twice(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The claim is the interlock, and reading the record is not one.

        ``run_investigation_in_process`` is reachable from the launcher's fallback
        thread and, once ``DATABASE_URI`` appears, from the queue worker as well.
        A read-then-run would investigate the same record twice — two full pipeline
        spends and two reports into one thread — because nothing else in this path
        is exclusive.
        """
        register_notifier(notifier)
        record = make_record()
        runs: list[str] = []

        def _counting_runner(trigger: dict[str, Any]) -> dict[str, Any]:
            runs.append(str(trigger.get("alert_name")))
            return {"report": "root cause: a bad deploy"}

        for _attempt in range(2):
            run_investigation_in_process(
                store, record.id, runner=_counting_runner, artifacts_dir=tmp_path
            )

        assert len(runs) == 1
        assert notifier.finals == ["root cause: a bad deploy"]

    def test_a_rest_record_is_refused_rather_than_left_running(
        self, store, notifier, register_notifier, tmp_path
    ):
        """Wrong origin is a wiring bug, and it must not leave a row RUNNING forever.

        The claim has already moved the record off the queue by the time the origin
        is checked, so returning early would strand it: no worker will pick it up
        again and no timeout sweeps it.
        """
        register_notifier(notifier)
        record = store.create(
            clerk_org_id="org-test",
            trigger={"raw_alert": {"alert_text": "rest"}},
            origin=InvestigationOrigin.REST,
        )

        def _must_not_run(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            raise AssertionError("a REST record was run by the chat fallback")

        run_investigation_in_process(store, record.id, runner=_must_not_run, artifacts_dir=tmp_path)

        stored = store.get(record.id)
        assert stored.status is InvestigationStatus.FAILED
        assert stored.error == "wrong_origin"


class TestDeliveryTargetShape:
    def test_a_dm_target_needs_no_thread(self):
        """A DM has no thread timestamp; requiring one would refuse every DM."""
        target = ChatDeliveryTarget(platform="slack", channel_id="D_TEST")

        assert target.thread_ts is None
