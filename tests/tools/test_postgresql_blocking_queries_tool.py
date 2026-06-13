"""Tests for PostgreSQLBlockingQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostgreSQLBlockingQueriesTool import get_postgresql_blocking_queries
from tests.tools.conftest import BaseToolContract


class TestPostgreSQLBlockingQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_blocking_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_blocking_queries.__opensre_registered_tool__
    assert rt.name == "get_postgresql_blocking_queries"
    assert rt.source == "postgresql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "total_queries": 2,
        "queries": [
            {
                "pid": 12345,
                "username": "app_user",
                "application_name": "myapp",
                "client_addr": "192.168.1.100",
                "state": "active",
                "query_start": "2024-01-15 10:30:00",
                "duration_seconds": 15,
                "wait_event_type": "Lock",
                "wait_event": "transactionid",
                "blocking_pids": [12346],
                "query_truncated": "UPDATE large_table SET status = 'processed' WHERE id = 1",
            },
            {
                "pid": 12346,
                "username": "app_user",
                "application_name": "myapp",
                "client_addr": "192.168.1.100",
                "state": "active",
                "query_start": "2024-01-15 10:29:45",
                "duration_seconds": 30,
                "wait_event_type": "",
                "wait_event": "",
                "blocking_pids": [],
                "query_truncated": "UPDATE large_table SET status = 'pending' WHERE id = 1",
            },
        ],
    }
    with patch(
        "app.tools.PostgreSQLBlockingQueriesTool.get_blocking_queries", return_value=fake_result
    ):
        result = get_postgresql_blocking_queries(host="localhost", database="testdb")
    assert result["total_queries"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["duration_seconds"] == 15
    assert result["queries"][0]["blocking_pids"] == [12346]


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.PostgreSQLBlockingQueriesTool.get_blocking_queries",
        return_value={"source": "postgresql", "available": False, "error": "permission denied"},
    ):
        result = get_postgresql_blocking_queries(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "app.tools.PostgreSQLBlockingQueriesTool.get_blocking_queries",
        return_value={"source": "postgresql", "available": True, "queries": []},
    ):
        result = get_postgresql_blocking_queries(host="localhost")
    assert "default_db_warning" in result
    assert "postgres" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "app.tools.PostgreSQLBlockingQueriesTool.get_blocking_queries",
        return_value={"source": "postgresql", "available": True, "queries": []},
    ):
        result = get_postgresql_blocking_queries(host="localhost", database="mydb")
    assert "default_db_warning" not in result
