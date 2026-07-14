"""Background worker that runs queued investigations and stores their reports."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gateway.http.artifacts import upload_report_to_s3, write_local_report
from gateway.http.investigation_store import InvestigationStatus, InvestigationStore

# The worker is opt-in so API-only processes (and tests) never run the pipeline.
WORKER_ENABLED_ENV = "OPENSRE_INVESTIGATION_WORKER"

logger = logging.getLogger(__name__)

InvestigationRunner = Callable[[dict[str, Any]], dict[str, Any]]


def _run_pipeline(trigger: dict[str, Any]) -> dict[str, Any]:
    from tools.investigation.capability import (
        resolve_investigation_context,
        run_investigation_payload,
    )

    raw_alert = trigger.get("raw_alert") or {}
    investigation_metadata = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=trigger.get("alert_name"),
        pipeline_name=trigger.get("pipeline_name"),
        severity=trigger.get("severity"),
    )
    return run_investigation_payload(
        raw_alert=raw_alert,
        investigation_metadata=investigation_metadata,
    )


class InvestigationWorker:
    """Claims queued investigations one at a time and persists their artifacts."""

    def __init__(
        self,
        store: InvestigationStore,
        *,
        runner: InvestigationRunner = _run_pipeline,
        poll_interval_seconds: float = 2.0,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._poll_interval_seconds = poll_interval_seconds
        self._artifacts_dir = artifacts_dir
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        """Process one queued investigation; return whether one was claimed."""
        record = self._store.claim_next_queued()
        if record is None:
            return False
        try:
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
        return True

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._loop, name="InvestigationWorker", daemon=True)
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
                logger.exception("[investigations] worker iteration failed")
                self._stop_event.wait(self._poll_interval_seconds)


_worker_lock = threading.Lock()
_worker: InvestigationWorker | None = None


def worker_enabled() -> bool:
    return os.getenv(WORKER_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def ensure_worker_started(store: InvestigationStore) -> InvestigationWorker | None:
    """Start the process-wide worker on first call; no-op unless enabled by env."""
    global _worker
    if not worker_enabled():
        return None
    with _worker_lock:
        if _worker is None:
            _worker = InvestigationWorker(store)
            _worker.start()
            logger.info("[investigations] worker started")
        return _worker
