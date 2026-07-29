"""Tests for the Neon/Postgres control-plane store."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.db import SCHEMA
from platform.deployment_multi_tenant.lambda_control_plane.db.db_client import ControlPlaneDbClient
from platform.deployment_multi_tenant.lambda_control_plane.db.schema import MIGRATIONS
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import (
    AgentRunSource,
    AgentRunStatus,
    DeploymentDesiredState,
    TenantApiCredential,
)


class _FakeCursor:
    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database
        self._row: tuple[Any, ...] | None = None
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None) -> None:
        self._database.queries.append((sql, params))
        self._row = self._database.rows.pop(0) if self._database.rows else None
        self.rowcount = self._database.rowcounts.pop(0) if self._database.rowcounts else 0

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [self._row] if self._row is not None else []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._database)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _FakeDatabase:
    def __init__(self) -> None:
        self.queries: list[tuple[str, Any]] = []
        self.rows: list[tuple[Any, ...] | None] = []
        self.rowcounts: list[int] = []
        self.gets = 0
        self.puts = 0
        self.connection = _FakeConnection(self)


def _install_fake_psycopg2(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[type, _FakeDatabase]:
    database = _FakeDatabase()

    class _FakePool:
        def __init__(
            self,
            _minconn: int,
            _maxconn: int,
            dsn: str,
            **_kwargs: Any,
        ) -> None:
            self.dsn = dsn

        def getconn(self) -> _FakeConnection:
            database.gets += 1
            return database.connection

        def putconn(self, _connection: _FakeConnection) -> None:
            database.puts += 1

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    psycopg2_module = types.ModuleType("psycopg2")
    psycopg2_module.pool = pool_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_module)
    monkeypatch.setitem(sys.modules, "psycopg2.pool", pool_module)
    return _FakePool, database


def _run_row(
    *,
    organization_id: str = "org_123",
    status: AgentRunStatus = AgentRunStatus.QUEUED,
) -> tuple[Any, ...]:
    now = datetime.now(UTC)
    return (
        "run_123",
        organization_id,
        AgentRunSource.API.value,
        "event_123",
        "Investigate",
        status.value,
        None,
        None,
        None,
        None,
        0,
        now,
        now,
    )


def test_schema_supports_metadata_only_credentials_dedup_and_leases() -> None:
    assert "tenant_deployments" in SCHEMA
    assert "tenant_api_credentials" in SCHEMA
    assert "agent_runs_source_event" in SCHEMA
    assert "WHERE source_event_id IS NOT NULL" in SCHEMA
    assert "lease_expires_at TIMESTAMPTZ" in SCHEMA
    assert "secret_arn TEXT NOT NULL" in SCHEMA
    assert "secret_value" not in SCHEMA
    assert "plaintext" not in SCHEMA
    assert MIGRATIONS[0] == ("0001_initial", SCHEMA)


def test_enqueue_agent_run_is_idempotent_for_stable_source_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, database = _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")
    database.rows.append(_run_row())

    run = store.enqueue_agent_run(
        organization_id="org_123",
        source=AgentRunSource.API,
        source_event_id="event_123",
        prompt="Investigate",
    )

    sql, params = database.queries[-1]
    assert "ON CONFLICT (organization_id, source, source_event_id)" in sql
    assert "DO UPDATE SET updated_at = agent_runs.updated_at" in sql
    assert params[1:] == (
        "org_123",
        AgentRunSource.API.value,
        "event_123",
        "Investigate",
        AgentRunStatus.QUEUED.value,
    )
    assert run.id == "run_123"


def test_claim_oldest_available_agent_run_is_tenant_scoped_lease_based_and_skip_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, database = _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")
    database.rows.append(_run_row(status=AgentRunStatus.RUNNING))

    claimed = store.claim_oldest_available_agent_run(
        organization_id="org_123",
        worker_id="worker_123",
        lease_duration=timedelta(seconds=45),
    )

    sql, params = database.queries[-1]
    assert "organization_id = %s" in sql
    assert "lease_expires_at < now()" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert params == (
        AgentRunStatus.RUNNING.value,
        "worker_123",
        45.0,
        "org_123",
        AgentRunStatus.QUEUED.value,
        AgentRunStatus.RUNNING.value,
    )
    assert claimed is not None
    assert claimed.organization_id == "org_123"


def test_non_positive_lease_is_rejected_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, database = _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")
    query_count = len(database.queries)

    with pytest.raises(ValueError, match="positive"):
        store.claim_oldest_available_agent_run(
            organization_id="org_123",
            worker_id="worker_123",
            lease_duration=timedelta(0),
        )

    assert len(database.queries) == query_count


def test_finalize_owned_agent_run_requires_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")

    with pytest.raises(ValueError, match="terminal"):
        store.finalize_owned_agent_run(
            run_id="run_123",
            worker_id="worker_123",
            status=AgentRunStatus.RUNNING,
        )


def test_api_credential_query_persists_only_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, database = _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")
    now = datetime.now(UTC)
    row = (
        "key_123",
        "org_123",
        "arn:aws:secretsmanager:eu-west-1:123:secret:public-api",
        True,
        now,
        now,
        None,
    )
    database.rows.append(row)
    credential = TenantApiCredential(
        key_id=row[0],
        organization_id=row[1],
        secret_arn=row[2],
        enabled=True,
        created_at=now,
        updated_at=now,
    )

    saved = store.upsert_tenant_api_credential(credential)

    sql, params = database.queries[-1]
    assert "secret_arn" in sql
    assert "secret_value" not in sql
    assert params == (*row[:6], None)
    assert saved == credential
    assert database.gets == database.puts


def test_count_deployments_with_running_desired_state_can_exclude_current_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, database = _install_fake_psycopg2(monkeypatch)
    store = ControlPlaneDbClient("postgresql://example/db")
    database.rows.append((2,))

    count = store.count_deployments_with_running_desired_state(
        exclude_organization_id="org_123",
    )

    sql, params = database.queries[-1]
    assert "desired_state = %s" in sql
    assert "organization_id <> %s" in sql
    assert params == (
        DeploymentDesiredState.RUNNING.value,
        "org_123",
        "org_123",
    )
    assert count == 2
