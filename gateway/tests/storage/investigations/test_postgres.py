from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from gateway.core.storage.investigations.postgres import (
    PostgresInvestigationStore,
    _bounded_connect_timeout,
)
from gateway.core.storage.investigations.store import InvestigationStatus


def _install_fake_psycopg2(monkeypatch: pytest.MonkeyPatch) -> type:
    class _FakeCursor:
        #: (sql, params) for every statement, so a test can assert on the query.
        executed: list[tuple[str, Any]] = []
        #: Rows handed back by ``fetchone``, in order; exhausted means "no row".
        rows: list[Any] = []

        def execute(self, sql: str, params: Any = None) -> None:
            _FakeCursor.executed.append((sql, params))

        def fetchone(self) -> Any:
            return _FakeCursor.rows.pop(0) if _FakeCursor.rows else None

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

        def __init__(self, minconn: int, maxconn: int, dsn: str, **kwargs: Any) -> None:
            self.minconn = minconn
            self.maxconn = maxconn
            self.dsn = dsn
            self.kwargs = kwargs
            self.connection = _FakeConnection()
            self.gets = 0
            self.puts = 0
            _FakePool.instances.append(self)

        def getconn(self) -> _FakeConnection:
            self.gets += 1
            return self.connection

        def putconn(self, _conn: _FakeConnection) -> None:
            self.puts += 1

    def _parse_dsn(dsn: str) -> dict[str, str]:
        """Stand-in for psycopg2.extensions.parse_dsn; query args are all we read."""
        query = dsn.partition("?")[2]
        return dict(item.split("=", 1) for item in query.split("&") if "=" in item)

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    extensions_module = types.ModuleType("psycopg2.extensions")
    extensions_module.parse_dsn = _parse_dsn  # type: ignore[attr-defined]
    psycopg2_module = types.ModuleType("psycopg2")
    psycopg2_module.pool = pool_module  # type: ignore[attr-defined]
    psycopg2_module.extensions = extensions_module  # type: ignore[attr-defined]
    _FakeCursor.executed = []
    _FakeCursor.rows = []
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_module)
    monkeypatch.setitem(sys.modules, "psycopg2.pool", pool_module)
    # Registered explicitly: the fake psycopg2 module has no __path__, so
    # ``from psycopg2.extensions import parse_dsn`` cannot resolve the submodule.
    monkeypatch.setitem(sys.modules, "psycopg2.extensions", extensions_module)
    return _FakePool, _FakeCursor


def test_one_pool_and_every_connection_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool_cls, _ = _install_fake_psycopg2(monkeypatch)

    store = PostgresInvestigationStore("postgresql://example/db")
    store.get("missing-id")
    store.claim_next_queued()

    assert len(fake_pool_cls.instances) == 1
    pool = fake_pool_cls.instances[0]
    assert pool.dsn == "postgresql://example/db"
    # Three operations (schema, get, claim): each borrowed and returned once.
    assert pool.gets == 3
    assert pool.puts == 3


def test_pool_connects_under_a_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """libpq waits forever without connect_timeout, and the pool opens connections
    inside __init__ — a blackholed address would wedge every request queued on the
    store lock in gateway/web/investigations.py, not just the first."""
    # Arrange
    fake_pool_cls, _ = _install_fake_psycopg2(monkeypatch)

    # Act
    PostgresInvestigationStore("postgresql://example/db")

    # Assert
    kwargs = fake_pool_cls.instances[0].kwargs
    assert 0 < kwargs["connect_timeout"] <= 30
    # Keepalives are what detect a silently dropped socket after connect.
    assert kwargs["keepalives"] == 1
    assert kwargs["keepalives_idle"] > 0


def test_dsn_connect_timeout_is_honoured_inside_the_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators tune through DATABASE_URL; the clamp still owns the bound."""
    # Arrange
    fake_pool_cls, _ = _install_fake_psycopg2(monkeypatch)

    # Act
    PostgresInvestigationStore("postgresql://example/db?connect_timeout=7")

    # Assert
    assert fake_pool_cls.instances[0].kwargs["connect_timeout"] == 7


@pytest.mark.parametrize(
    ("dsn_value", "expected"),
    [
        (None, 10),  # unset
        ("", 10),
        ("0", 10),  # libpq's "wait forever"
        ("not-a-number", 10),
        ("1", 1),  # an aggressive value fails fast, which cannot cause a hang
        ("7", 7),
        ("3600", 30),  # an hour is not a bound
    ],
)
def test_connect_timeout_is_clamped(dsn_value: str | None, expected: int) -> None:
    assert _bounded_connect_timeout(dsn_value) == expected


def _raise_query_error() -> None:
    raise RuntimeError("query exploded")


def test_connection_returned_to_pool_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool_cls, _ = _install_fake_psycopg2(monkeypatch)
    store = PostgresInvestigationStore("postgresql://example/db")
    pool = fake_pool_cls.instances[0]

    with pytest.raises(RuntimeError), store._connection():
        _raise_query_error()

    assert pool.puts == pool.gets


def _queued_row(investigation_id: str = "inv-1", org: str = "org_a") -> tuple[Any, ...]:
    """A row shaped like ``_COLUMNS``, already flipped to cancelled."""
    return (
        investigation_id,
        org,
        "ws-1",
        "cancelled",
        "{}",
        None,
        None,
        None,
        "2026-08-01T00:00:00+00:00",
        "2026-08-01T00:00:01+00:00",
    )


def test_cancel_guards_on_org_and_queued_status_in_one_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cancel must be a conditional update, not read-then-write.

    A worker can claim a queued investigation at any moment. If cancel checked
    the status in Python and then wrote, a claim landing between the two would
    be silently overwritten and a running investigation marked cancelled. The
    guard has to live in the WHERE clause so the database arbitrates.
    """
    # Arrange
    _, cursor_cls = _install_fake_psycopg2(monkeypatch)
    store = PostgresInvestigationStore("postgresql://example/db")
    cursor_cls.rows.append(_queued_row())

    # Act
    record = store.cancel("inv-1", clerk_org_id="org_a")

    # Assert
    sql, params = cursor_cls.executed[-1]
    normalized = " ".join(sql.split()).lower()
    assert normalized.startswith("update investigations")
    assert "where id = %s and clerk_org_id = %s and status = %s" in normalized
    assert "returning" in normalized
    assert params == ("cancelled", "inv-1", "org_a", "queued")
    assert record is not None
    assert record.status is InvestigationStatus.CANCELLED


def test_cancel_returns_none_when_no_row_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    """No matching row means already claimed, already terminal, or another org."""
    # Arrange
    _, cursor_cls = _install_fake_psycopg2(monkeypatch)
    store = PostgresInvestigationStore("postgresql://example/db")
    assert cursor_cls.rows == []

    # Act
    record = store.cancel("inv-1", clerk_org_id="org_a")

    # Assert
    assert record is None
