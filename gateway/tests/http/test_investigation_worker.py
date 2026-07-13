from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gateway.http.artifacts import ARTIFACTS_BUCKET_ENV, upload_report_to_s3
from gateway.http.investigation_store import InMemoryInvestigationStore, InvestigationStatus
from gateway.http.worker import WORKER_ENABLED_ENV, InvestigationWorker, worker_enabled


def _queued(store: InMemoryInvestigationStore, org: str = "org_a") -> str:
    record = store.create(clerk_org_id=org, trigger={"raw_alert": {"alert_name": "cpu"}})
    return record.id


def test_run_once_completes_and_writes_local_report(tmp_path: Path) -> None:
    store = InMemoryInvestigationStore()
    investigation_id = _queued(store)
    worker = InvestigationWorker(
        store,
        runner=lambda _trigger: {"report": "disk full", "root_cause": "log growth"},
        artifacts_dir=tmp_path,
    )

    assert worker.run_once() is True

    record = store.get(investigation_id)
    assert record is not None
    assert record.status is InvestigationStatus.COMPLETED
    assert record.report_local_path is not None
    saved = json.loads(Path(record.report_local_path).read_text())
    assert saved["root_cause"] == "log growth"
    # No artifacts bucket configured in tests: local file only.
    assert record.report_s3_key is None


def test_run_once_marks_failed_on_runner_error(tmp_path: Path) -> None:
    store = InMemoryInvestigationStore()
    investigation_id = _queued(store)

    def runner(_trigger: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("pipeline exploded")

    worker = InvestigationWorker(store, runner=runner, artifacts_dir=tmp_path)

    assert worker.run_once() is True

    record = store.get(investigation_id)
    assert record is not None
    assert record.status is InvestigationStatus.FAILED
    assert record.error == "RuntimeError"


def test_run_once_returns_false_when_queue_empty(tmp_path: Path) -> None:
    worker = InvestigationWorker(
        InMemoryInvestigationStore(), runner=lambda _t: {}, artifacts_dir=tmp_path
    )
    assert worker.run_once() is False


def test_claim_is_oldest_first_and_single_delivery() -> None:
    store = InMemoryInvestigationStore()
    first = _queued(store)
    second = _queued(store)

    claimed_one = store.claim_next_queued()
    claimed_two = store.claim_next_queued()

    assert claimed_one is not None and claimed_one.id == first
    assert claimed_one.status is InvestigationStatus.RUNNING
    assert claimed_two is not None and claimed_two.id == second
    assert store.claim_next_queued() is None


def test_worker_disabled_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKER_ENABLED_ENV, raising=False)
    assert worker_enabled() is False
    monkeypatch.setenv(WORKER_ENABLED_ENV, "1")
    assert worker_enabled() is True


def test_upload_report_returns_none_without_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ARTIFACTS_BUCKET_ENV, raising=False)
    local = tmp_path / "report.json"
    local.write_text("{}")

    assert upload_report_to_s3(local, org_id="org_a", investigation_id="inv-1") is None


def test_upload_report_builds_org_scoped_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads: list[tuple[str, str, str]] = []

    class _FakeS3:
        def upload_file(self, filename: str, bucket: str, key: str) -> None:
            uploads.append((filename, bucket, key))

    import boto3

    monkeypatch.setenv(ARTIFACTS_BUCKET_ENV, "opensre-artifacts")
    monkeypatch.setattr(boto3, "client", lambda _service: _FakeS3())
    local = tmp_path / "report.json"
    local.write_text("{}")

    key = upload_report_to_s3(local, org_id="org_a", investigation_id="inv-1")

    assert key == "org_a/inv-1/report.json"
    assert uploads == [(str(local), "opensre-artifacts", "org_a/inv-1/report.json")]


def test_ensure_worker_started_is_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.http import worker as worker_mod
    from gateway.http.worker import ensure_worker_started

    monkeypatch.delenv(WORKER_ENABLED_ENV, raising=False)
    monkeypatch.setattr(worker_mod, "_worker", None)

    assert ensure_worker_started(InMemoryInvestigationStore()) is None


def test_ensure_worker_started_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.http import worker as worker_mod
    from gateway.http.worker import ensure_worker_started

    monkeypatch.setenv(WORKER_ENABLED_ENV, "1")
    monkeypatch.setattr(worker_mod, "_worker", None)
    store = InMemoryInvestigationStore()

    first = ensure_worker_started(store)
    second = ensure_worker_started(store)

    assert first is not None
    assert second is first
    first.stop()
