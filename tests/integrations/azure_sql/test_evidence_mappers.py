"""Evidence mapper coverage for the Azure SQL integration tools."""

from __future__ import annotations

from typing import Any

from integrations.azure_sql.tools.azure_sql_current_queries_tool import (
    _map_get_azure_sql_current_queries,
)
from integrations.azure_sql.tools.azure_sql_resource_stats_tool import (
    _map_get_azure_sql_resource_stats,
)
from integrations.azure_sql.tools.azure_sql_server_status_tool import (
    _map_get_azure_sql_server_status,
)
from integrations.azure_sql.tools.azure_sql_slow_queries_tool import (
    _map_get_azure_sql_slow_queries,
)
from integrations.azure_sql.tools.azure_sql_wait_stats_tool import (
    _map_get_azure_sql_wait_stats,
)
from tools.registry import get_registered_tool


def _entry(evidence: dict[str, Any]) -> Any:
    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list) and entries
    return entries[0]


class TestCurrentQueries:
    def test_records_query_count_and_longest_duration(self) -> None:
        evidence: dict[str, Any] = {}
        output = {
            "available": True,
            "threshold_seconds": 1,
            "total_queries": 2,
            "queries": [
                {"session_id": 1, "duration_seconds": 5, "status": "running"},
                {"session_id": 2, "duration_seconds": 12, "status": "running"},
            ],
        }

        _map_get_azure_sql_current_queries(evidence, output, {})

        entry = _entry(evidence)
        assert entry["source"] == "get_azure_sql_current_queries"
        assert entry["label"] == "Azure SQL Current Queries"
        assert entry["summary"] == "2 running query/queries, max duration 12s"

    def test_no_entry_when_unavailable_or_empty(self) -> None:
        for output in (
            {"available": False},
            {"available": True, "queries": []},
            {"available": True, "queries": "not-a-list"},
            {},
        ):
            evidence: dict[str, Any] = {}
            _map_get_azure_sql_current_queries(evidence, output, {})
            assert evidence == {}


class TestResourceStats:
    def test_records_sample_count_and_throttling_risk(self) -> None:
        evidence: dict[str, Any] = {}
        output = {
            "available": True,
            "window_minutes": 30,
            "total_samples": 29,
            "throttling_risk": "high",
            "samples": [{"avg_cpu_percent": 88.0}],
        }

        _map_get_azure_sql_resource_stats(evidence, output, {})

        entry = _entry(evidence)
        assert entry["source"] == "get_azure_sql_resource_stats"
        assert entry["summary"] == "29 sample(s), throttling risk high"

    def test_no_entry_when_no_samples(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_azure_sql_resource_stats(evidence, {"available": True, "total_samples": 0}, {})
        assert evidence == {}


class TestServerStatus:
    def test_records_connections_cpu_and_tier(self) -> None:
        evidence: dict[str, Any] = {}
        output = {
            "available": True,
            "service_tier": {"edition": "Standard", "service_objective": "S1"},
            "connections": {"total": 42, "active": 7, "idle": 35},
            "resource_utilization": {
                "avg_cpu_percent": 15.5,
                "avg_memory_usage_percent": 22.0,
            },
            "database_size_mb": 1024.0,
        }

        _map_get_azure_sql_server_status(evidence, output, {})

        entry = _entry(evidence)
        assert entry["source"] == "get_azure_sql_server_status"
        assert entry["summary"] == "42 connection(s), 7 active, CPU 15.5%, memory 22.0%, tier S1"

    def test_no_entry_when_unavailable(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_azure_sql_server_status(evidence, {"available": False}, {})
        assert evidence == {}


class TestSlowQueries:
    def test_records_count_and_slowest_average(self) -> None:
        evidence: dict[str, Any] = {}
        output = {
            "available": True,
            "threshold_ms": 1000,
            "total_queries": 2,
            "queries": [
                {"query_hash": "a", "avg_time_ms": 1500.0},
                {"query_hash": "b", "avg_time_ms": 3400.5},
            ],
        }

        _map_get_azure_sql_slow_queries(evidence, output, {})

        entry = _entry(evidence)
        assert entry["source"] == "get_azure_sql_slow_queries"
        assert entry["summary"] == "2 slow query/queries, slowest avg 3400ms"

    def test_no_entry_when_no_queries(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_azure_sql_slow_queries(evidence, {"available": True, "queries": []}, {})
        assert evidence == {}


class TestWaitStats:
    def test_records_wait_count_and_top_wait(self) -> None:
        evidence: dict[str, Any] = {}
        output = {
            "available": True,
            "total_wait_types": 2,
            "waits": [
                {"wait_type": "PAGEIOLATCH_SH", "wait_time_ms": 5000, "waiting_tasks_count": 3},
                {"wait_type": "LCK_M_S", "wait_time_ms": 12000, "waiting_tasks_count": 1},
            ],
        }

        _map_get_azure_sql_wait_stats(evidence, output, {})

        entry = _entry(evidence)
        assert entry["source"] == "get_azure_sql_wait_stats"
        assert entry["summary"] == "2 wait type(s), top LCK_M_S 12000ms"

    def test_no_entry_when_no_waits(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_azure_sql_wait_stats(evidence, {"available": True, "waits": []}, {})
        assert evidence == {}


class TestToolRegistration:
    """Every azure_sql investigation tool carries its evidence mapper."""

    def test_all_tools_carry_mappers(self) -> None:
        mapping = {
            "get_azure_sql_current_queries": _map_get_azure_sql_current_queries,
            "get_azure_sql_resource_stats": _map_get_azure_sql_resource_stats,
            "get_azure_sql_server_status": _map_get_azure_sql_server_status,
            "get_azure_sql_slow_queries": _map_get_azure_sql_slow_queries,
            "get_azure_sql_wait_stats": _map_get_azure_sql_wait_stats,
        }
        for name, mapper in mapping.items():
            registered = get_registered_tool(name)
            assert registered is not None
            assert registered.evidence_mapper is mapper
