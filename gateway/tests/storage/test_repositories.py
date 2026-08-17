"""``Repositories`` is the one place that chooses storage and shares one database."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from config.constants.gateway import DATABASE_URL_ENV
from gateway.core.storage.events.repository import (
    InMemoryHandledSlackEventRepository,
    PostgresHandledSlackEventRepository,
)
from gateway.core.storage.investigations.repository import (
    InMemoryInvestigationRepository,
    PostgresInvestigationRepository,
)
from gateway.core.storage.repositories import Repositories


def _install_fake_psycopg2(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """A psycopg2 stand-in that records every pool it opens."""
    pools: list[Any] = []

    class _FakeConnection:
        def cursor(self) -> _FakeConnection:
            return self

        def execute(self, *_args: Any) -> None:
            return None

        def __enter__(self) -> _FakeConnection:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    class _FakePool:
        def __init__(self, _min: int, _max: int, dsn: str, **_kwargs: Any) -> None:
            self.dsn = dsn
            pools.append(self)

        def getconn(self) -> _FakeConnection:
            return _FakeConnection()

        def putconn(self, _conn: Any) -> None:
            return None

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    ext_module = types.ModuleType("psycopg2.extensions")
    ext_module.parse_dsn = lambda _dsn: {}  # type: ignore[attr-defined]
    root = types.ModuleType("psycopg2")
    root.pool = pool_module  # type: ignore[attr-defined]
    root.extensions = ext_module  # type: ignore[attr-defined]
    for name, module in (
        ("psycopg2", root),
        ("psycopg2.pool", pool_module),
        ("psycopg2.extensions", ext_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return pools


def test_no_dsn_gives_process_local_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    repositories = Repositories.from_env()

    assert repositories.shared is False
    assert isinstance(repositories.investigations, InMemoryInvestigationRepository)
    assert isinstance(repositories.handled_slack_events, InMemoryHandledSlackEventRepository)


def test_a_dsn_gives_postgres_repositories_over_one_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both repositories sit on the same database, so the process opens one pool."""
    pools = _install_fake_psycopg2(monkeypatch)
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://example/db")

    repositories = Repositories.from_env()

    assert repositories.shared is True
    assert isinstance(repositories.investigations, PostgresInvestigationRepository)
    assert isinstance(repositories.handled_slack_events, PostgresHandledSlackEventRepository)
    assert len(pools) == 1  # schema ran for both repositories through one pool
    assert pools[0].dsn == "postgresql://example/db"
