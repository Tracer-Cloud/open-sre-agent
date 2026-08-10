"""Chat investigation worker that runs detached investigations and posts results back."""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from gateway.core.chat import (
    ChatDeliveryTarget,
    get_chat_notifier_registry,
)
from gateway.core.investigations.artifacts import write_local_report
from gateway.core.investigations.stage_notifier import StageNotifyingProgressTracker
from gateway.core.runtime.concurrency import TurnConcurrencyGate
from gateway.core.storage.investigations.store import (
    InvestigationOrigin,
    InvestigationStatus,
    InvestigationStore,
)
from platform.deployment_contracts.models import SizeProfile
from platform.observability.render.progress import set_progress_override

logger = logging.getLogger(__name__)

InvestigationRunner = Any  # Using Any to avoid circular import with worker.py


class ChatInvestigationWorker:
    """Claims chat-origin investigations and posts results back to the originating thread."""

    def __init__(
        self,
        store: InvestigationStore,
        *,
        runner: InvestigationRunner | None = None,
        poll_interval_seconds: float = 2.0,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or self._default_runner
        self._poll_interval_seconds = poll_interval_seconds
        self._artifacts_dir = artifacts_dir
        self._stop_event = threading.Event()
        # Chat investigations get their own concurrency gate, separate from main turns
        self._gate = TurnConcurrencyGate.for_profile(SizeProfile.SMALL)

    def _default_runner(self, trigger: dict[str, Any]) -> dict[str, Any]:
        """Default investigation pipeline runner."""
        from core.agent_harness import AgentSession
        from tools.investigation.capability import resolve_investigation_context

        raw_alert = trigger.get("raw_alert") or {}
        investigation_metadata = resolve_investigation_context(
            raw_alert=raw_alert,
            alert_name=trigger.get("alert_name"),
            severity=trigger.get("severity"),
        )
        return (
            AgentSession()
            .investigate(
                raw_alert,
                investigation_metadata=investigation_metadata,
            )
            .as_dict()
        )

    def run_once(self) -> bool:
        """Process one chat-origin investigation; return whether one was claimed."""
        record = self._store.claim_next_queued(origin=InvestigationOrigin.CHAT)
        if record is None:
            return False

        self._gate.acquire()
        try:
            self._process_investigation(record)
        finally:
            self._gate.release()

        return True

    def _process_investigation(self, record: Any) -> None:
        """Process a single investigation record."""
        # Extract delivery target from trigger
        trigger = record.trigger or {}
        delivery_target_data = trigger.get("delivery_target")
        if not delivery_target_data:
            logger.warning("[chat-investigations] no delivery target for %s", record.id)
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="no_delivery_target",
            )
            return

        try:
            delivery_target = ChatDeliveryTarget(**delivery_target_data)
        except Exception as exc:
            logger.warning(
                "[chat-investigations] invalid delivery target for %s: %s",
                record.id,
                type(exc).__name__,
            )
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="invalid_delivery_target",
            )
            return

        # Get notifier for this platform
        registry = get_chat_notifier_registry()
        notifier = registry.get(delivery_target.platform)
        if notifier is None:
            logger.warning(
                "[chat-investigations] no notifier for platform %s (investigation %s)",
                delivery_target.platform,
                record.id,
            )
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="no_notifier",
            )
            return

        # D5: Rebuild and bind storage scope per record - fail closed if scope cannot be reconstructed
        scope_data = trigger.get("scope", {})
        org_id = scope_data.get("org_id")
        actor_id = scope_data.get("actor_id")

        if not org_id:
            logger.warning("[chat-investigations] no scope data for %s", record.id)
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="unbound_scope",
            )
            notifier.report_failure(delivery_target, "Investigation failed", record.id)
            # Mark the origin message as failed (best-effort, never propagate)
            with contextlib.suppress(Exception):
                notifier.mark_origin_failed(delivery_target)
            return

        # Rebuild storage scope
        scope = StorageScope(
            principal=Principal.org(org_id),
            actor=Actor(actor_id or "worker"),
        )

        with bound_storage_scope(scope):
            # Stage updates go to the thread that asked, via the notifier resolved above.
            # The tracker takes all three as arguments: nothing inside the pipeline knows
            # the investigation id, and this thread has no delivery target bound.
            tracker = StageNotifyingProgressTracker(
                notifier=notifier,
                target=delivery_target,
                investigation_id=record.id,
            )
            set_progress_override(tracker)
            try:
                result = self._runner(record.trigger)

                # Extract report text for chat delivery
                report_text = str(
                    result.get("slack_message") or result.get("report") or "Investigation completed"
                )

                # Write local report
                local_path = write_local_report(record.id, result, base_dir=self._artifacts_dir)

                # D6: Delivery-first ordering with bool result
                delivered = notifier.deliver_final(delivery_target, report_text, record.id)

                # Mark as completed/failed based on delivery outcome
                self._store.finish(
                    record.id,
                    status=InvestigationStatus.COMPLETED
                    if delivered
                    else InvestigationStatus.FAILED,
                    report_local_path=str(local_path),
                    error=None if delivered else "delivery_failed",
                )

                # Mark the origin message based on delivery outcome (best-effort)
                if delivered:
                    with contextlib.suppress(Exception):
                        notifier.mark_origin_complete(delivery_target)
                else:
                    with contextlib.suppress(Exception):
                        notifier.mark_origin_failed(delivery_target)

                logger.info(
                    "[chat-investigations] completed %s (delivered=%s)", record.id, delivered
                )

            except Exception as exc:
                logger.exception("[chat-investigations] failed %s", record.id)
                self._store.finish(
                    record.id,
                    status=InvestigationStatus.FAILED,
                    error=type(exc).__name__,
                )
                # Report generic failure to chat (no exception details per CWE-209)
                notifier.report_failure(delivery_target, "Investigation failed", record.id)
                # Mark the origin message as failed (best-effort, never propagate)
                with contextlib.suppress(Exception):
                    notifier.mark_origin_failed(delivery_target)

            finally:
                # Clear the progress override
                set_progress_override(None)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._loop, name="ChatInvestigationWorker", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.run_once():
                    self._stop_event.wait(self._poll_interval_seconds)
            except Exception:
                logger.exception("[chat-investigations] worker iteration failed")
                self._stop_event.wait(self._poll_interval_seconds)


def run_investigation_in_process(
    store: InvestigationStore,
    investigation_id: str,
    *,
    runner: InvestigationRunner | None = None,
    artifacts_dir: Path | None = None,
) -> None:
    """Run a single chat investigation in-process as fallback when no worker is available.

    This provides the no-DSN fallback path per scope decision 1.
    """
    # Claim, rather than read-then-mark. ``claim`` returns None when the record is
    # not queued, so a record another runner is already working is skipped instead
    # of investigated twice — and it does not go through ``finish``, which records
    # a *terminal* status and clears the artifact fields on the way past.
    record = store.claim(investigation_id)
    if record is None:
        logger.warning(
            "[chat-investigations] investigation %s is not queued; not running in-process",
            investigation_id,
        )
        return

    if record.origin != InvestigationOrigin.CHAT:
        logger.warning(
            "[chat-investigations] investigation %s has origin %s, not chat",
            investigation_id,
            record.origin,
        )
        store.finish(
            investigation_id,
            status=InvestigationStatus.FAILED,
            error="wrong_origin",
        )
        return

    # Create a temporary worker instance to reuse the processing logic
    worker = ChatInvestigationWorker(store, runner=runner, artifacts_dir=artifacts_dir)

    try:
        worker._process_investigation(record)
    except Exception as exc:
        logger.exception("[chat-investigations] in-process run failed for %s", investigation_id)
        store.finish(
            investigation_id,
            status=InvestigationStatus.FAILED,
            error=type(exc).__name__,
        )
