"""Background worker that runs queued investigations and stores their reports."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.constants.gateway import (
    INVESTIGATION_WORKER_ENABLED_ENV,
    INVESTIGATION_WORKERS_ENV,
)
from gateway.core.billing.credits_client import CreditsOutcome, consume_credits
from gateway.core.storage.investigations.repository import (
    InvestigationRepository,
    InvestigationStatus,
)
from gateway.web.artifacts import upload_report_to_s3, write_local_report

# The worker is opt-in so API-only processes (and tests) never run the pipeline.
WORKER_ENABLED_ENV = INVESTIGATION_WORKER_ENABLED_ENV

logger = logging.getLogger(__name__)

InvestigationRunner = Callable[[dict[str, Any]], dict[str, Any]]


def _run_pipeline(trigger: dict[str, Any]) -> dict[str, Any]:
    from core.agent_harness import AgentSession
    from tools.investigation.capability import (
        resolve_investigation_context,
        run_investigation_payload,
    )

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
            runner=run_investigation_payload,
            investigation_metadata=investigation_metadata,
        )
        .as_dict()
    )


class InvestigationWorker:
    """Claims queued investigations and persists their artifacts.

    ``pool_size`` threads each claim and run one investigation at a time; the
    claim is atomic (an in-memory lock, or Postgres ``FOR UPDATE SKIP LOCKED``),
    so no record is processed twice. Each running investigation still takes a
    turn-gate slot, so ``pool_size`` bounds threads while the gate bounds total
    concurrency across chat and scheduled work.
    """

    def __init__(
        self,
        store: InvestigationRepository,
        *,
        runner: InvestigationRunner = _run_pipeline,
        poll_interval_seconds: float = 2.0,
        artifacts_dir: Path | None = None,
        pool_size: int = 1,
    ) -> None:
        self._store = store
        self._runner = runner
        self._poll_interval_seconds = poll_interval_seconds
        self._artifacts_dir = artifacts_dir
        self._pool_size = max(1, pool_size)
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        """Process one queued investigation; return whether one was claimed."""
        record = self._store.claim_next_queued()
        if record is None:
            return False
        # A self-hosted runtime with no webapp URL deliberately disables
        # metering. Once a webapp is configured, any inability to obtain a
        # trustworthy admission decision must fail closed before model work.
        outcome = consume_credits(record.clerk_org_id, reason="investigation")
        if outcome is CreditsOutcome.DENIED:
            logger.info("[investigations] denied %s: insufficient credits", record.id)
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="insufficient_credits",
            )
            return True
        if outcome not in (CreditsOutcome.ALLOWED, CreditsOutcome.DISABLED):
            logger.error("[investigations] metering unavailable for %s", record.id)
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error="credit_metering_unavailable",
            )
            return True
        from infrastructure.process.turn_capacity import queued_turn_slot
        from infrastructure.turn_host.concurrency import process_turn_gate

        # Already claimed from the queue, so it waits for a slot rather than
        # being dropped — the same policy scheduled runs take.
        with queued_turn_slot(process_turn_gate()):
            self._investigate(record)
        return True

    def _investigate(self, record: Any) -> None:
        """Run one claimed investigation and record its outcome; capacity is the caller's."""
        try:
            from infrastructure.analytics.investigation_tracker import track_investigation
            from infrastructure.analytics.source import EntrypointSource, TriggerMode
            from infrastructure.analytics.usage_context import (
                bound_usage_context,
                ensure_process_session_id,
            )

            org_id = (record.clerk_org_id or "").strip() or None
            with (
                bound_usage_context(
                    organization_id=org_id,
                    # Process session groups HTTP investigations for this worker;
                    # keep investigation_id as the per-run identifier.
                    session_id=ensure_process_session_id(),
                ),
                track_investigation(
                    entrypoint=EntrypointSource.REMOTE_HTTP,
                    trigger_mode=TriggerMode.SERVICE_RUNTIME,
                    investigation_id=record.id,
                    investigation_target=str((record.trigger or {}).get("alert_name") or "")
                    or None,
                ),
            ):
                result = self._runner(record.trigger)
            local_path = write_local_report(record.id, result, base_dir=self._artifacts_dir)
            s3_key = upload_report_to_s3(
                local_path, org_id=record.clerk_org_id, investigation_id=record.id
            )
            self._store.finish(
                record.id,
                status=InvestigationStatus.COMPLETED,
                report_local_path=str(local_path),
                report_s3_key=s3_key,
            )
            logger.info("[investigations] completed %s", record.id)
        except Exception as exc:
            logger.exception("[investigations] failed %s", record.id)
            self._store.finish(
                record.id,
                status=InvestigationStatus.FAILED,
                error=type(exc).__name__,
            )

    def start(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(target=self._loop, name=f"InvestigationWorker-{i}", daemon=True)
            for i in range(self._pool_size)
        ]
        for thread in threads:
            thread.start()
        return threads

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.run_once():
                    self._stop_event.wait(self._poll_interval_seconds)
            except Exception:
                logger.exception("[investigations] worker iteration failed")
                self._stop_event.wait(self._poll_interval_seconds)


_worker_lock = threading.Lock()
_worker: InvestigationWorker | None = None


def worker_enabled() -> bool:
    return os.getenv(WORKER_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _configured_worker_count() -> int:
    """Concurrent investigation threads from the env; one when unset or invalid."""
    raw = os.getenv(INVESTIGATION_WORKERS_ENV, "").strip()
    if not raw:
        return 1
    try:
        count = int(raw)
    except ValueError:
        logger.warning("[investigations] ignoring invalid %s=%r", INVESTIGATION_WORKERS_ENV, raw)
        return 1
    if count < 1:
        logger.warning(
            "[investigations] ignoring non-positive %s=%r", INVESTIGATION_WORKERS_ENV, raw
        )
        return 1
    return count


def ensure_worker_started(store: InvestigationRepository) -> InvestigationWorker | None:
    """Start the process-wide worker on first call; no-op unless enabled by env."""
    global _worker
    if not worker_enabled():
        return None
    with _worker_lock:
        if _worker is None:
            count = _configured_worker_count()
            _worker = InvestigationWorker(store, pool_size=count)
            _worker.start()
            logger.info("[investigations] worker started (%d thread(s))", count)
        return _worker
