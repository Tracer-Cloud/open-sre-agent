"""Tests for the schema migration custom-resource handler."""

from __future__ import annotations

from typing import Any

import pytest

from platform.deployment_multi_tenant.lambda_control_plane import migration_runtime


def test_create_applies_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_client(
        database_url: str,
        *,
        initialize_schema: bool,
    ) -> object:
        calls.append((database_url, initialize_schema))
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/opensre")
    monkeypatch.setattr(migration_runtime, "ControlPlaneDbClient", fake_client)

    response = migration_runtime.lambda_handler({"RequestType": "Create"}, object())

    assert calls == [("postgresql://database.example/opensre", True)]
    assert response == {"PhysicalResourceId": "opensre-control-plane-schema"}


def test_delete_preserves_schema_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("delete must not connect to Postgres")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(migration_runtime, "ControlPlaneDbClient", unexpected_client)

    response = migration_runtime.lambda_handler({"RequestType": "Delete"}, object())

    assert response == {"PhysicalResourceId": "opensre-control-plane-schema"}


def test_create_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(
        "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN",
        raising=False,
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        migration_runtime.lambda_handler({"RequestType": "Create"}, object())


def test_migration_retries_operational_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OperationalError(Exception):
        pass

    OperationalError.__module__ = "psycopg2"
    attempts = 0
    sleeps: list[int] = []

    def flaky_client(_database_url: str, *, initialize_schema: bool) -> object:
        nonlocal attempts
        assert initialize_schema is True
        attempts += 1
        if attempts < 3:
            raise OperationalError
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/opensre")
    monkeypatch.setattr(migration_runtime, "ControlPlaneDbClient", flaky_client)
    monkeypatch.setattr(migration_runtime.time, "sleep", sleeps.append)

    migration_runtime.lambda_handler({"RequestType": "Update"}, object())

    assert attempts == 3
    assert sleeps == [1, 2]
