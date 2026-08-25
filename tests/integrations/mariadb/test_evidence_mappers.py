"""Evidence mapper tests for the MariaDB tools."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.mariadb.tools.mariadb_innodb_status_tool import (
    _map_get_mariadb_innodb_status,
)
from integrations.mariadb.tools.mariadb_process_list_tool import (
    _map_get_mariadb_process_list,
)
from integrations.mariadb.tools.mariadb_replication_tool import (
    _map_get_mariadb_replication_status,
)
from integrations.mariadb.tools.mariadb_slow_queries_tool import (
    _map_get_mariadb_slow_queries,
)
from integrations.mariadb.tools.mariadb_status_tool import (
    _map_get_mariadb_global_status,
)
from tools.registry import get_registered_tool


def test_global_status_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "mariadb",
        "available": True,
        "metrics": {
            "Threads_connected": "10",
            "Threads_running": "2",
            "Uptime": "86400",
        },
    }

    _map_get_mariadb_global_status(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_mariadb_global_status",
            "label": "MariaDB Global Status",
            "summary": "3 metrics",
            "url": None,
            "snippet": None,
        }
    ]


def test_global_status_mapper_records_nothing_on_error() -> None:
    evidence: dict[str, object] = {}

    _map_get_mariadb_global_status(
        evidence,
        {"source": "mariadb", "available": False, "error": "connection timeout"},
        {},
    )

    assert "catalog_entries" not in evidence


def test_innodb_status_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    status_text = (
        "=====================================\n"
        "BUFFER POOL AND MEMORY\n"
        "Total memory allocated 137428992\n"
        "====================================="
    )
    output = {"source": "mariadb", "available": True, "innodb_status": status_text}

    _map_get_mariadb_innodb_status(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_mariadb_innodb_status",
            "label": "MariaDB InnoDB Status",
            "summary": f"{len(status_text)} chars",
            "url": None,
            "snippet": None,
        }
    ]


def test_innodb_status_mapper_records_nothing_on_error() -> None:
    evidence: dict[str, object] = {}

    _map_get_mariadb_innodb_status(
        evidence,
        {"source": "mariadb", "available": False, "error": "connection timeout"},
        {},
    )

    assert "catalog_entries" not in evidence


def test_process_list_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "mariadb",
        "available": True,
        "total_processes": 2,
        "processes": [
            {
                "id": 12,
                "user": "app",
                "host": "10.0.0.5:51820",
                "database": "orders",
                "command": "Query",
                "time_secs": 42,
                "state": "Sending data",
                "query": "SELECT * FROM orders WHERE customer_id = 881",
            },
            {
                "id": 13,
                "user": "app",
                "host": "10.0.0.6:51821",
                "database": "orders",
                "command": "Query",
                "time_secs": 5,
                "state": "executing",
                "query": "UPDATE orders SET status = 'shipped' WHERE id = 42",
            },
        ],
    }

    _map_get_mariadb_process_list(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_mariadb_process_list",
            "label": "MariaDB Process List",
            "summary": "2 active processes",
            "url": None,
            "snippet": None,
        }
    ]


def test_process_list_mapper_records_nothing_when_idle() -> None:
    evidence: dict[str, object] = {}

    _map_get_mariadb_process_list(
        evidence,
        {"source": "mariadb", "available": True, "total_processes": 0, "processes": []},
        {},
    )

    assert "catalog_entries" not in evidence


def test_replication_status_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "mariadb",
        "available": True,
        "channels": [
            {
                "Slave_IO_Running": "Yes",
                "Slave_SQL_Running": "Yes",
                "Seconds_Behind_Master": 0,
                "Master_Host": "db-primary.internal",
                "Connection_name": "",
            },
            {
                "Slave_IO_Running": "No",
                "Slave_SQL_Running": "Yes",
                "Seconds_Behind_Master": None,
                "Last_Error": "Could not execute Update_rows_v1 event",
                "Master_Host": "db-secondary.internal",
                "Connection_name": "analytics",
            },
        ],
    }

    _map_get_mariadb_replication_status(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_mariadb_replication_status",
            "label": "MariaDB Replication Status",
            "summary": "2 replication channels",
            "url": None,
            "snippet": None,
        }
    ]


def test_replication_status_mapper_records_nothing_when_not_a_replica() -> None:
    evidence: dict[str, object] = {}

    _map_get_mariadb_replication_status(
        evidence,
        {
            "source": "mariadb",
            "available": True,
            "note": "This server is not configured as a replica.",
            "channels": [],
        },
        {},
    )

    assert "catalog_entries" not in evidence


def test_slow_queries_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "mariadb",
        "available": True,
        "total_queries": 2,
        "queries": [
            {
                "digest_text": "SELECT * FROM orders WHERE customer_id = ?",
                "count": 542,
                "avg_time_ms": 812.4,
                "total_time_ms": 440320.8,
                "rows_examined": 100000,
                "rows_sent": 542,
            },
            {
                "digest_text": "UPDATE orders SET status = ? WHERE id = ?",
                "count": 12,
                "avg_time_ms": 45.2,
                "total_time_ms": 542.4,
                "rows_examined": 12,
                "rows_sent": 12,
            },
        ],
    }

    _map_get_mariadb_slow_queries(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_mariadb_slow_queries",
            "label": "MariaDB Slow Queries",
            "summary": "2 slow queries",
            "url": None,
            "snippet": None,
        }
    ]


def test_slow_queries_mapper_records_nothing_when_performance_schema_disabled() -> None:
    evidence: dict[str, object] = {}

    _map_get_mariadb_slow_queries(
        evidence,
        {
            "source": "mariadb",
            "available": True,
            "note": "performance_schema is disabled. Enable it in my.cnf to collect slow query data.",
            "queries": [],
        },
        {},
    )

    assert "catalog_entries" not in evidence


@pytest.mark.parametrize(
    ("tool_name", "mapper"),
    [
        ("get_mariadb_global_status", _map_get_mariadb_global_status),
        ("get_mariadb_innodb_status", _map_get_mariadb_innodb_status),
        ("get_mariadb_process_list", _map_get_mariadb_process_list),
        ("get_mariadb_replication_status", _map_get_mariadb_replication_status),
        ("get_mariadb_slow_queries", _map_get_mariadb_slow_queries),
    ],
)
def test_registered_tool_carries_the_mapper(tool_name: str, mapper: Any) -> None:
    registered_tool = get_registered_tool(tool_name)

    assert registered_tool is not None
    assert registered_tool.evidence_mapper is mapper
