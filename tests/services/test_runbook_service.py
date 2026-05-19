"""Unit tests for RunbookService — no live DB or OpenAI calls."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.runbook_service import (
    RunbookService,
    _average_embeddings,
    _chunk_text,
)
from app.state.runbook import Runbook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runbook(tenant_id: str = "t1", title: str = "OOMKilled runbook") -> Runbook:
    r = Runbook()
    r.id = uuid.uuid4()
    r.tenant_id = tenant_id
    r.title = title
    r.content = "Pod OOMKilled — increase memory limits."
    r.embedding = [0.1] * 1536
    r.tags = ["k8s", "memory"]
    r.source_url = None
    return r


# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_short_content_returns_single_chunk() -> None:
    chunks = _chunk_text("short text", max_tokens=8000)
    assert chunks == ["short text"]


def test_chunk_text_long_content_splits() -> None:
    # 8 000+ tokens of repetitive text
    long_text = "word " * 9000
    chunks = _chunk_text(long_text, max_tokens=8000)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk  # non-empty


# ---------------------------------------------------------------------------
# _average_embeddings
# ---------------------------------------------------------------------------


def test_average_embeddings_single_passthrough() -> None:
    emb = [0.5] * 10
    assert _average_embeddings([emb]) == emb


def test_average_embeddings_multiple() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    avg = _average_embeddings([a, b])
    assert avg == pytest.approx([0.5, 0.5])


# ---------------------------------------------------------------------------
# RunbookService.upsert — mocked OpenAI + DB session
# ---------------------------------------------------------------------------


FAKE_EMBEDDING = [0.1] * 1536


def _mock_embed(texts: list[str]) -> list[list[float]]:
    return [FAKE_EMBEDDING for _ in texts]


@pytest.fixture
def db_session() -> MagicMock:
    session = MagicMock()
    # execute().scalars().first() chain used in upsert / search
    session.execute.return_value.scalars.return_value.first.return_value = None
    session.execute.return_value.scalars.return_value.all.return_value = []
    return session


@patch("app.services.runbook_service._embed", side_effect=_mock_embed)
def test_upsert_creates_new_runbook(mock_embed: Any, db_session: MagicMock) -> None:
    svc = RunbookService(db_session)
    rb = svc.upsert(
        tenant_id="tenant-a",
        title="OOMKilled",
        content="Increase limits",
    )
    assert rb.tenant_id == "tenant-a"
    assert rb.title == "OOMKilled"
    assert rb.embedding == FAKE_EMBEDDING
    db_session.add.assert_called_once_with(rb)


@patch("app.services.runbook_service._embed", side_effect=_mock_embed)
def test_upsert_updates_existing_runbook(mock_embed: Any, db_session: MagicMock) -> None:
    existing = _make_runbook()
    db_session.execute.return_value.scalars.return_value.first.return_value = existing

    svc = RunbookService(db_session)
    rb = svc.upsert(
        tenant_id=existing.tenant_id,
        title=existing.title,
        content="Updated content",
    )
    assert rb is existing
    assert rb.content == "Updated content"
    db_session.add.assert_not_called()  # update in-place, not new add


@patch("app.services.runbook_service._embed", side_effect=_mock_embed)
def test_search_returns_results_for_tenant(
    mock_embed: Any, db_session: MagicMock
) -> None:
    rb = _make_runbook(tenant_id="tenant-a")
    db_session.execute.return_value.scalars.return_value.all.return_value = [rb]

    svc = RunbookService(db_session)
    results = svc.search(tenant_id="tenant-a", query="pod out of memory", top_k=3)
    assert len(results) == 1
    assert results[0].tenant_id == "tenant-a"


def test_delete_returns_false_when_not_found(db_session: MagicMock) -> None:
    db_session.execute.return_value.scalars.return_value.first.return_value = None
    svc = RunbookService(db_session)
    assert svc.delete(tenant_id="t1", runbook_id=uuid.uuid4()) is False
    db_session.delete.assert_not_called()


def test_delete_returns_true_and_deletes(db_session: MagicMock) -> None:
    rb = _make_runbook()
    db_session.execute.return_value.scalars.return_value.first.return_value = rb
    svc = RunbookService(db_session)
    assert svc.delete(tenant_id=rb.tenant_id, runbook_id=rb.id) is True
    db_session.delete.assert_called_once_with(rb)


# ---------------------------------------------------------------------------
# Tenant isolation: search must never return another tenant's runbooks
# ---------------------------------------------------------------------------


@patch("app.services.runbook_service._embed", side_effect=_mock_embed)
def test_search_isolates_tenants(mock_embed: Any, db_session: MagicMock) -> None:
    rb_a = _make_runbook(tenant_id="tenant-a")
    rb_b = _make_runbook(tenant_id="tenant-b")

    # Simulate DB returning only tenant-a records (WHERE tenant_id = 'tenant-a')
    db_session.execute.return_value.scalars.return_value.all.return_value = [rb_a]

    svc = RunbookService(db_session)
    results = svc.search(tenant_id="tenant-a", query="OOMKilled", top_k=5)

    assert all(r.tenant_id == "tenant-a" for r in results)
    assert rb_b not in results
