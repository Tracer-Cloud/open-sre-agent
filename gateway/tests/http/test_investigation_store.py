"""Unit tests for InMemoryInvestigationStore contract."""

from __future__ import annotations

from gateway.http.investigation_store import InMemoryInvestigationStore, InvestigationStatus


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
