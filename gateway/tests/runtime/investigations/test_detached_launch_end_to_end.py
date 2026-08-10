"""The path from a chat turn to a queued record, and the borders that keep it apart.

The suites beside this one exercise the worker in isolation. This one starts where
a real turn starts — a slash command through :class:`GatewayTurnHandler` — because
the two seams that carry a chat turn into the detached path are both contextvars,
and a contextvar that nobody binds fails silently: the turn simply runs the
foreground pipeline it was built to avoid.
"""

from __future__ import annotations

import contextvars
import io
import logging
import threading
from typing import Any

import pytest
from rich.console import Console

from config.constants import PLATFORM_SLACK
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from gateway.core.chat import (
    ChatDeliveryTarget,
    bound_delivery_target,
    get_chat_notifier_registry,
    get_current_delivery_target,
)
from gateway.core.investigations import detached_launcher
from gateway.core.investigations.chat_worker import ChatInvestigationWorker
from gateway.core.investigations.detached_launcher import (
    bind_gateway_detached_launcher,
    launch_detached_investigation,
)
from gateway.core.investigations.launch_record import DetachedLaunchRecord
from gateway.core.runtime.concurrency import (
    TurnConcurrencyGate,
    reset_process_turn_gate_for_tests,
    set_process_turn_gate,
)
from gateway.core.runtime.turn_handler import GatewayTurnHandler
from gateway.core.storage.investigations.store import (
    InvestigationOrigin,
    InvestigationStatus,
)
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    RecordingGatewaySink,
    headless_slash_ports,
)

# How long a test waits on a background thread it expects to reach a barrier.
_THREAD_WAIT_SECONDS = 5.0


def _run_gateway_turn(text: str) -> RecordingGatewaySink:
    """Drive one gateway turn the way the Slack dispatcher does."""
    sink = RecordingGatewaySink()
    handler = GatewayTurnHandler(
        console=Console(file=io.StringIO(), force_terminal=False),
        slash_ports_factory=headless_slash_ports,
    )
    handler(text, SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    return sink


class TestDeliveryContext:
    def test_the_target_is_unbound_again_after_the_turn(self, delivery_target):
        """One process serves every thread; a leaked target posts into the wrong one."""
        assert get_current_delivery_target() is None

        with bound_delivery_target(delivery_target):
            assert get_current_delivery_target() == delivery_target

        assert get_current_delivery_target() is None

    def test_the_target_reaches_a_thread_launched_from_the_turn(self, delivery_target):
        """The in-process fallback runs on a new thread, which starts context-empty.

        ``copy_context()`` is taken on the submitting thread for exactly this
        reason — see the fleet-search precedent. Without it the run resolves
        integrations unbound and has nowhere to post.
        """
        seen: list[ChatDeliveryTarget | None] = []

        with bound_delivery_target(delivery_target):
            ctx = contextvars.copy_context()
            thread = threading.Thread(
                target=ctx.run, args=(lambda: seen.append(get_current_delivery_target()),)
            )
            thread.start()
            thread.join(_THREAD_WAIT_SECONDS)

        assert seen == [delivery_target]


class TestQueueOriginIsolation:
    def test_each_worker_claims_only_its_own_origin(self, store):
        """REST and chat share one table; a REST worker that stole a chat record
        would run it and post the report into an HTTP response nobody is reading.
        """
        chat = store.create(
            clerk_org_id="org-test",
            trigger={"alert_text": "chat"},
            origin=InvestigationOrigin.CHAT,
        )
        rest = store.create(
            clerk_org_id="org-test",
            trigger={"alert_text": "rest"},
            origin=InvestigationOrigin.REST,
        )

        assert store.claim_next_queued(origin=InvestigationOrigin.CHAT).id == chat.id
        assert store.claim_next_queued(origin=InvestigationOrigin.REST).id == rest.id
        assert store.claim_next_queued(origin=InvestigationOrigin.CHAT) is None

    def test_a_record_is_only_claimable_once(self, store):
        """``claim`` is what stops the in-process fallback re-running a live record."""
        record = store.create(
            clerk_org_id="org-test",
            trigger={"alert_text": "chat"},
            origin=InvestigationOrigin.CHAT,
        )

        assert store.claim(record.id) is not None
        assert store.claim(record.id) is None


class TestNotifierRegistry:
    def test_a_notifier_is_found_only_under_the_platform_it_registered(
        self, notifier, register_notifier
    ):
        """Registration and lookup must agree on the key.

        They are written in different packages — the transport registers, the
        launcher looks up — and a mismatch degrades to "investigations are not
        supported here", which reads like a deliberate product decision.
        """
        register_notifier(notifier)
        registry = get_chat_notifier_registry()

        assert registry.get(PLATFORM_SLACK) is notifier
        assert registry.get("discord") is None
        assert registry.get("telegram") is None


class TestSlashRoutingGoesDetached:
    def test_a_slash_investigate_is_queued_rather_than_run(
        self, delivery_target, notifier, register_notifier, _no_real_pipeline
    ):
        """The turn that used to blow the 240s budget now returns a receipt."""
        register_notifier(notifier)

        launch_record = DetachedLaunchRecord()
        with (
            bound_delivery_target(delivery_target),
            bind_gateway_detached_launcher(launch_record),
        ):
            sink = _run_gateway_turn("/investigate alert:High CPU on checkout")

        assert len(notifier.acks) == 1
        assert "queued" in notifier.acks[0].message
        assert sink.finalized is not None
        assert _no_real_pipeline == [notifier.acks[0].investigation_id]

    def test_a_sample_alert_takes_the_same_path(
        self, delivery_target, notifier, register_notifier, _no_real_pipeline
    ):
        """``/investigate generic`` reaches the launcher through a second adapter.

        Two entrypoints, one seam. Pinned separately because the sample path has
        its own function in the adapter and was missed once already.
        """
        register_notifier(notifier)

        launch_record = DetachedLaunchRecord()
        with (
            bound_delivery_target(delivery_target),
            bind_gateway_detached_launcher(launch_record),
        ):
            _run_gateway_turn("/investigate generic")

        assert len(notifier.acks) == 1
        assert len(_no_real_pipeline) == 1

    def test_a_turn_with_no_launcher_bound_runs_locally(
        self, monkeypatch: pytest.MonkeyPatch, _no_real_pipeline
    ):
        """The REPL shares this adapter. Detaching there would strand the answer.

        Nothing binds a launcher outside the gateway, so ``/investigate`` in a
        terminal must still run the foreground pipeline and print its report.
        """
        ran_locally = False

        def _fake_local(**_kwargs: Any) -> dict[str, Any]:
            nonlocal ran_locally
            ran_locally = True
            return {"status": "completed", "report": "local investigation"}

        monkeypatch.setattr(
            "tools.investigation.session_runner.run_investigation_for_session", _fake_local
        )

        _run_gateway_turn("/investigate alert:Local alert")

        assert ran_locally
        assert _no_real_pipeline == []


class TestAckPrecedesTheReport:
    def test_the_launcher_returns_before_the_pipeline_finishes(
        self, delivery_target, notifier, register_notifier, monkeypatch: pytest.MonkeyPatch
    ):
        """The whole point of detaching: the turn ends while the run continues.

        The fake pipeline blocks until this test releases it, so a launcher that
        waited for the result would hang here rather than fail an assertion.
        """
        register_notifier(notifier)
        started = threading.Event()
        release = threading.Event()

        def _blocking_run(store: Any, investigation_id: str, target: Any) -> None:
            _ = (store, investigation_id, target)
            started.set()
            release.wait(_THREAD_WAIT_SECONDS)

        monkeypatch.setattr(detached_launcher, "_run_investigation_background", _blocking_run)

        with bound_delivery_target(delivery_target):
            result = launch_detached_investigation("checkout latency is up")

        assert result.accepted
        assert len(notifier.acks) == 1
        assert started.wait(_THREAD_WAIT_SECONDS), "the pipeline never started"
        release.set()


class TestChatWorkerConcurrency:
    def test_the_chat_worker_does_not_wait_on_the_turn_gate(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Chat investigations hold their own gate.

        A detached run outlives the turn that asked for it, so parking it behind
        the process turn gate would let one investigation block every inbound
        message for minutes — the failure the REST worker's separate gate exists
        to prevent, one surface over.
        """
        reset_process_turn_gate_for_tests()
        occupied = TurnConcurrencyGate(1)
        assert occupied.try_acquire() is True
        set_process_turn_gate(occupied)
        try:
            register_notifier(notifier)
            record = make_record()
            ran = threading.Event()

            def _runner(trigger: dict[str, Any]) -> dict[str, Any]:
                _ = trigger
                ran.set()
                return {"report": "done"}

            worker = ChatInvestigationWorker(store, runner=_runner, artifacts_dir=tmp_path)
            thread = threading.Thread(target=worker.run_once)
            thread.start()

            assert ran.wait(_THREAD_WAIT_SECONDS), "the chat worker blocked on the turn gate"
            thread.join(_THREAD_WAIT_SECONDS)
            assert store.get(record.id).status is InvestigationStatus.COMPLETED
        finally:
            occupied.release()
            reset_process_turn_gate_for_tests()
