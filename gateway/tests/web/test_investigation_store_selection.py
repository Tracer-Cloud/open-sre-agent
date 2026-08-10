"""Which env name selects the durable investigation store.

The chart emits ``DATABASE_URI`` and the code used to read only
``DATABASE_URL``, so a deployment with Postgres wired up silently kept the
process-local store: queued investigations died on restart and the web and
gateway pods could not see each other's work. Nothing failed loudly.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.datastore import DATABASE_URI_ENV, DATABASE_URL_ENV
from gateway.core.investigations.storage_utils import reset_investigation_store_for_tests
from gateway.core.storage.investigations.store import InMemoryInvestigationStore
from gateway.web import investigations


class _FakePostgresStore:
    """Stands in for the real store so no connection is attempted."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    reset_investigation_store_for_tests()
    monkeypatch.setattr(
        "gateway.core.storage.investigations.postgres.PostgresInvestigationStore",
        _FakePostgresStore,
    )
    monkeypatch.delenv(DATABASE_URI_ENV, raising=False)
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    yield
    reset_investigation_store_for_tests()


def test_chart_env_name_selects_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URI_ENV, "postgresql://u:p@host:5432/db")
    store = investigations._store()
    assert isinstance(store, _FakePostgresStore)
    assert store.dsn == "postgresql://u:p@host:5432/db"


def test_hosted_platform_env_name_still_selects_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://u:p@railway:5432/db")
    store = investigations._store()
    assert isinstance(store, _FakePostgresStore)
    assert store.dsn == "postgresql://u:p@railway:5432/db"


def test_chart_env_name_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URI_ENV, "postgresql://u:p@chosen:5432/db")
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://u:p@injected:5432/db")
    store = investigations._store()
    assert isinstance(store, _FakePostgresStore)
    assert store.dsn == "postgresql://u:p@chosen:5432/db"


def test_no_dsn_keeps_the_in_memory_store() -> None:
    assert isinstance(investigations._store(), InMemoryInvestigationStore)
