from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from gateway.http.postgres_store import PostgresInvestigationStore


def _install_fake_psycopg2(monkeypatch: pytest.MonkeyPatch) -> type:
    class _FakeCursor:
        def execute(self, _sql: str, _params: Any = None) -> None:
            return None

        def fetchone(self) -> None:
            return None

        def __enter__(self) -> _FakeCursor:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class _FakeConnection:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def __enter__(self) -> _FakeConnection:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class _FakePool:
        instances: list[_FakePool] = []

        def __init__(self, minconn: int, maxconn: int, dsn: str) -> None:
            self.minconn = minconn
            self.maxconn = maxconn
            self.dsn = dsn
            self.connection = _FakeConnection()
            self.gets = 0
            self.puts = 0
            _FakePool.instances.append(self)

        def getconn(self) -> _FakeConnection:
            self.gets += 1
            return self.connection

        def putconn(self, _conn: _FakeConnection) -> None:
            self.puts += 1

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    psycopg2_module = types.ModuleType("psycopg2")
    psycopg2_module.pool = pool_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_module)
    monkeypatch.setitem(sys.modules, "psycopg2.pool", pool_module)
    return _FakePool


def test_one_pool_and_every_connection_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool_cls = _install_fake_psycopg2(monkeypatch)

    store = PostgresInvestigationStore("postgresql://example/db")
    store.get("missing-id")
    store.claim_next_queued()

    assert len(fake_pool_cls.instances) == 1
    pool = fake_pool_cls.instances[0]
    assert pool.dsn == "postgresql://example/db"
    # Three operations (schema, get, claim): each borrowed and returned once.
    assert pool.gets == 3
    assert pool.puts == 3


def _raise_query_error() -> None:
    raise RuntimeError("query exploded")


def test_connection_returned_to_pool_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool_cls = _install_fake_psycopg2(monkeypatch)
    store = PostgresInvestigationStore("postgresql://example/db")
    pool = fake_pool_cls.instances[0]

    with pytest.raises(RuntimeError), store._connection():
        _raise_query_error()

    assert pool.puts == pool.gets
