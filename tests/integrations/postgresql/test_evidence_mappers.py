"""Evidence mapper coverage for the postgresql tools (#5540)."""

from __future__ import annotations

from typing import Any

from integrations.postgresql.tools.postgresql_current_queries_tool import (
    _map_get_postgresql_current_queries,
)
from integrations.postgresql.tools.postgresql_locks_tool import (
    _map_get_postgresql_lock_status,
)
from integrations.postgresql.tools.postgresql_replication_status_tool import (
    _map_get_postgresql_replication_status,
)
from integrations.postgresql.tools.postgresql_server_status_tool import (
    _map_get_postgresql_server_status,
)
from integrations.postgresql.tools.postgresql_slow_queries_tool import (
    _map_get_postgresql_slow_queries,
)
from integrations.postgresql.tools.postgresql_table_stats_tool import (
    _map_get_postgresql_table_stats,
)


class TestMapPostgresqlCurrentQueries:
    def test_records_count_and_longest_duration(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_current_queries(
            evidence,
            {
                "available": True,
                "total_queries": 2,
                "queries": [
                    {"pid": 1, "state": "active", "duration_seconds": 8},
                    {"pid": 2, "state": "active", "duration_seconds": 21},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_current_queries"
        assert entries[0]["summary"] == "2 running query/queries, max duration 21s"

    def test_records_nothing_when_unavailable_or_empty(self) -> None:
        for output in (
            {"available": False, "error": "boom"},
            {"available": True, "queries": []},
            {"available": True, "queries": "not-a-list"},
        ):
            evidence: dict[str, Any] = {}
            _map_get_postgresql_current_queries(evidence, output, {})
            assert "catalog_entries" not in evidence


class TestMapPostgresqlLockStatus:
    def test_records_blocked_count_and_longest_wait(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_lock_status(
            evidence,
            {
                "available": True,
                "blocked_queries": [
                    {"blocked_pid": 1, "wait_seconds": 15, "locktype": "relation"},
                    {"blocked_pid": 2, "wait_seconds": 40, "locktype": "tuple"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_lock_status"
        assert entries[0]["summary"] == "2 blocked query/queries, longest wait 40s"

    def test_records_nothing_when_no_blocked_queries(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_postgresql_lock_status(
            evidence, {"available": True, "blocked_queries": [], "lock_summary": []}, {}
        )
        assert "catalog_entries" not in evidence


class TestMapPostgresqlReplicationStatus:
    def test_records_replica_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_replication_status(
            evidence,
            {
                "available": True,
                "is_primary": True,
                "replica_count": 2,
                "replicas": [{"client_addr": "10.0.0.1"}, {"client_addr": "10.0.0.2"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_replication_status"
        assert entries[0]["summary"] == "2 streaming replica(s)"

    def test_records_replica_not_primary(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_replication_status(
            evidence,
            {"available": True, "is_primary": False, "pg_is_in_recovery": True},
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert "replica" in entries[0]["summary"]

    def test_records_nothing_when_primary_with_no_replicas(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_postgresql_replication_status(
            evidence, {"available": True, "is_primary": True, "replica_count": 0}, {}
        )
        assert "catalog_entries" not in evidence


class TestMapPostgresqlServerStatus:
    def test_records_connections_and_cache_hit(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_server_status(
            evidence,
            {
                "available": True,
                "connections": {"total": 42, "active": 7},
                "database_stats": {"cache_hit_ratio_percent": 99.3},
                "uptime": "3 days",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_server_status"
        assert "42 connection(s)" in entries[0]["summary"]
        assert "cache hit 99.3%" in entries[0]["summary"]

    def test_records_nothing_when_no_metric_fields(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_postgresql_server_status(evidence, {"available": True}, {})
        assert "catalog_entries" not in evidence


class TestMapPostgresqlSlowQueries:
    def test_records_count_and_slowest_mean(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_slow_queries(
            evidence,
            {
                "available": True,
                "extension_available": True,
                "total_queries": 2,
                "queries": [
                    {"queryid": "1", "mean_time_ms": 1500.0},
                    {"queryid": "2", "mean_time_ms": 3200.0},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_slow_queries"
        assert entries[0]["summary"] == "2 slow query/queries, slowest mean 3200ms"

    def test_records_note_when_extension_missing(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_slow_queries(
            evidence,
            {
                "available": True,
                "extension_available": False,
                "note": "pg_stat_statements extension is not installed.",
                "queries": [],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert "extension" in entries[0]["summary"]

    def test_records_nothing_when_no_slow_queries(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_postgresql_slow_queries(
            evidence, {"available": True, "extension_available": True, "queries": []}, {}
        )
        assert "catalog_entries" not in evidence


class TestMapPostgresqlTableStats:
    def test_records_count_and_largest_table(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_postgresql_table_stats(
            evidence,
            {
                "available": True,
                "total_tables": 2,
                "tables": [
                    {"schema": "public", "table_name": "users", "size": {"total_mb": 120.0}},
                    {"schema": "public", "table_name": "orders", "size": {"total_mb": 540.0}},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_postgresql_table_stats"
        assert entries[0]["summary"] == "2 table(s), largest orders 540MB"

    def test_records_nothing_when_no_tables(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_postgresql_table_stats(evidence, {"available": True, "tables": []}, {})
        assert "catalog_entries" not in evidence
