"""Cross-replica dedup: the first delivery wins, retries are refused.

Exercised against a fake cursor rather than a live database — the contract
under test is that ``claim`` reports the insert's ``rowcount`` and never drops
a delivery when the store is unreachable.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.transports.slack.persistence.event_dedup import (
    PostgresSlackEventDeduplicator,
)


class _FakeCursor:
    """Mimics ON CONFLICT DO NOTHING across replicas via one shared set."""

    def __init__(self, inserted: set[str], *, fail: bool = False) -> None:
        self._inserted = inserted
        self._fail = fail
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        if self._fail:
            raise RuntimeError("connection refused")
        if "INSERT INTO slack_handled_events" not in sql:
            self.rowcount = 0  # schema / retention statements claim nothing
            return
        assert params is not None
        event_id = str(params[0])
        self.rowcount = 0 if event_id in self._inserted else 1
        self._inserted.add(event_id)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(self, inserted: set[str], *, fail: bool = False) -> None:
        self._inserted = inserted
        self._fail = fail

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._inserted, fail=self._fail)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _deduplicator(inserted: set[str], *, fail: bool = False) -> PostgresSlackEventDeduplicator:
    """Build one without touching psycopg2 or running the schema."""
    dedup = PostgresSlackEventDeduplicator.__new__(PostgresSlackEventDeduplicator)

    from contextlib import contextmanager

    @contextmanager
    def _connection() -> Any:
        yield _FakeConnection(inserted, fail=fail)

    dedup._connection = _connection  # type: ignore[method-assign]
    return dedup


def test_first_delivery_is_claimed_and_the_retry_is_refused() -> None:
    # Arrange.
    dedup = _deduplicator(set())

    # Act.
    first = dedup.claim("Ev123")
    retry = dedup.claim("Ev123")

    # Assert.
    assert first is True
    assert retry is False


def test_a_retry_reaching_another_replica_is_still_refused() -> None:
    """The point of the shared store: two processes, one handled set."""
    # Arrange — separate instances, as two replicas would be.
    shared: set[str] = set()
    replica_a = _deduplicator(shared)
    replica_b = _deduplicator(shared)

    # Act.
    first = replica_a.claim("Ev123")
    retry_elsewhere = replica_b.claim("Ev123")

    # Assert.
    assert first is True
    assert retry_elsewhere is False


def test_distinct_events_are_each_claimed() -> None:
    # Arrange.
    dedup = _deduplicator(set())

    # Act / Assert.
    assert dedup.claim("Ev1") is True
    assert dedup.claim("Ev2") is True


def test_store_failure_admits_the_delivery(caplog: pytest.LogCaptureFixture) -> None:
    """Losing a real user message is worse than a possible duplicate."""
    # Arrange.
    dedup = _deduplicator(set(), fail=True)

    # Act.
    claimed = dedup.claim("Ev123")

    # Assert — admitted, and the failure is visible rather than swallowed.
    assert claimed is True
    assert "event dedup unavailable" in caplog.text
