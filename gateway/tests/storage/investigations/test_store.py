"""Unit tests for InMemoryInvestigationStore contract."""

from __future__ import annotations

from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationStatus,
)


def test_create_persists_workspace_id() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(
        clerk_org_id="org",
        trigger={"raw_alert": {}},
        workspace_id="ws_1",
    )

    loaded = store.get(record.id)
    assert loaded is not None
    assert loaded.workspace_id == "ws_1"
    assert loaded.status is InvestigationStatus.QUEUED


def test_get_returns_copy() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(clerk_org_id="org", trigger={})
    loaded = store.get(record.id)
    assert loaded is not None
    loaded.status = InvestigationStatus.COMPLETED

    again = store.get(record.id)
    assert again is not None
    assert again.status is InvestigationStatus.QUEUED


def test_finish_unknown_id_is_noop() -> None:
    store = InMemoryInvestigationStore()
    store.finish("missing", status=InvestigationStatus.FAILED, error="x")
    assert store.get("missing") is None


def test_finish_sets_artifact_fields() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(clerk_org_id="org", trigger={})
    store.claim_next_queued()
    store.finish(
        record.id,
        status=InvestigationStatus.COMPLETED,
        report_local_path="/tmp/r.json",
        report_s3_key="org/id/report.json",
    )

    done = store.get(record.id)
    assert done is not None
    assert done.report_local_path == "/tmp/r.json"
    assert done.report_s3_key == "org/id/report.json"
    assert done.status is InvestigationStatus.COMPLETED


def test_cancel_queued_investigation() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(clerk_org_id="org", trigger={})
    cancelled = store.cancel(record.id, clerk_org_id="org")
    assert cancelled is not None
    assert cancelled.status is InvestigationStatus.CANCELLED
    assert store.get(record.id) is not None
    assert store.get(record.id).status is InvestigationStatus.CANCELLED  # type: ignore[union-attr]


def test_cancel_running_investigation_is_rejected() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(clerk_org_id="org", trigger={})
    store.claim_next_queued()
    assert store.cancel(record.id, clerk_org_id="org") is None
    assert store.get(record.id).status is InvestigationStatus.RUNNING  # type: ignore[union-attr]


def test_cancel_rejects_wrong_org() -> None:
    store = InMemoryInvestigationStore()
    record = store.create(clerk_org_id="org_a", trigger={})
    assert store.cancel(record.id, clerk_org_id="org_b") is None
    assert store.get(record.id).status is InvestigationStatus.QUEUED  # type: ignore[union-attr]
