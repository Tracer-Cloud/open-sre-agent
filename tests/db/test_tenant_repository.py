"""Unit tests for TenantRepository row-level tenant isolation.

Uses an in-memory SQLite DB so no Postgres is needed.
Verifies that:
 - TenantRepository.query() always appends WHERE tenant_id = <current>
 - add() auto-stamps the current tenant
 - tenant-A queries cannot see tenant-B records and vice versa
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base, set_current_tenant
from app.db.models import Investigation
from app.db.repository import TenantRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_investigation(**kwargs) -> Investigation:
    defaults = {
        "thread_id": "t1",
        "alert_name": "Test Alert",
        "pipeline_name": "svc",
        "severity": "warning",
    }
    defaults.update(kwargs)
    return Investigation(**defaults)


def test_add_auto_stamps_tenant_id(db: Session) -> None:
    """add() must set tenant_id from the current context."""
    set_current_tenant("tenant-x")
    repo = TenantRepository(db, Investigation)
    inv = _make_investigation()
    repo.add(inv)
    db.flush()

    assert inv.tenant_id == "tenant-x"


def test_query_filters_by_current_tenant(db: Session) -> None:
    """query() must return only records for the active tenant."""
    set_current_tenant("tenant-a")
    TenantRepository(db, Investigation).add(_make_investigation(alert_name="A-Alert"))

    set_current_tenant("tenant-b")
    TenantRepository(db, Investigation).add(_make_investigation(alert_name="B-Alert"))
    db.flush()

    set_current_tenant("tenant-a")
    results = TenantRepository(db, Investigation).all()
    assert len(results) == 1
    assert results[0].alert_name == "A-Alert"
    assert results[0].tenant_id == "tenant-a"


def test_tenant_a_cannot_see_tenant_b_records(db: Session) -> None:
    """Core isolation test: insert for A and B, query as A, get only A."""
    set_current_tenant("tenant-a")
    TenantRepository(db, Investigation).add(
        _make_investigation(thread_id="a1", alert_name="A-Alert", pipeline_name="svc-a")
    )

    set_current_tenant("tenant-b")
    TenantRepository(db, Investigation).add(
        _make_investigation(
            thread_id="b1", alert_name="B-Alert", pipeline_name="svc-b", severity="critical"
        )
    )
    db.flush()

    set_current_tenant("tenant-a")
    repo = TenantRepository(db, Investigation)
    results = repo.all()

    assert len(results) == 1
    assert results[0].alert_name == "A-Alert"
    assert results[0].tenant_id == "tenant-a"


def test_tenant_b_cannot_see_tenant_a_records(db: Session) -> None:
    """Symmetric isolation: query as B returns only B's records."""
    set_current_tenant("tenant-a")
    TenantRepository(db, Investigation).add(_make_investigation(alert_name="A-Alert"))

    set_current_tenant("tenant-b")
    TenantRepository(db, Investigation).add(_make_investigation(alert_name="B-Alert"))
    db.flush()

    set_current_tenant("tenant-b")
    results = TenantRepository(db, Investigation).all()
    assert len(results) == 1
    assert results[0].alert_name == "B-Alert"
    assert results[0].tenant_id == "tenant-b"


def test_get_returns_none_for_cross_tenant_id(db: Session) -> None:
    """get(id) must not return a record that belongs to another tenant."""
    set_current_tenant("tenant-a")
    repo_a = TenantRepository(db, Investigation)
    inv = _make_investigation(alert_name="A-Alert")
    repo_a.add(inv)
    db.flush()
    inv_id = inv.id

    set_current_tenant("tenant-b")
    repo_b = TenantRepository(db, Investigation)
    result = repo_b.get(inv_id)
    assert result is None


def test_multiple_tenants_all_returns_correct_count(db: Session) -> None:
    """All three tenants co-exist; each sees only their own records."""
    for tenant in ("t1", "t2", "t3"):
        set_current_tenant(tenant)
        repo = TenantRepository(db, Investigation)
        for i in range(3):
            repo.add(_make_investigation(thread_id=f"{tenant}-{i}", alert_name=f"{tenant}-Alert-{i}"))
    db.flush()

    for tenant in ("t1", "t2", "t3"):
        set_current_tenant(tenant)
        results = TenantRepository(db, Investigation).all()
        assert len(results) == 3
        assert all(r.tenant_id == tenant for r in results)
