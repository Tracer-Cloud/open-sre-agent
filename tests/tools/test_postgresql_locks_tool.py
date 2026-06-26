"""Tests for PostgreSQLLocksTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.postgresql_locks_tool import get_postgresql_lock_status


class TestPostgreSQLLocksToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_lock_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_lock_status.__opensre_registered_tool__
    assert rt.name == "get_postgresql_lock_status"
    assert rt.source == "postgresql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "blocked_query_count": 1,
        "blocked_queries": [
            {
                "blocked_pid": 1234,
                "blocked_user": "app_user",
                "blocked_app": "myapp",
                "blocked_query": "UPDATE orders SET status = 'shipped' WHERE id = $1",
                "blocking_pid": 5678,
                "blocking_user": "batch_job",
                "blocking_app": "worker",
                "blocking_query": "SELECT * FROM orders FOR UPDATE",
                "wait_seconds": 42,
                "locktype": "relation",
                "relation": "orders",
            }
        ],
        "lock_summary": [
            {"locktype": "relation", "granted": 10, "waiting": 1},
            {"locktype": "transactionid", "granted": 5, "waiting": 0},
        ],
    }
    with patch("tools.postgresql_locks_tool.get_lock_status", return_value=fake_result):
        result = get_postgresql_lock_status(host="localhost", database="testdb")
    assert result["available"] is True
    assert result["blocked_query_count"] == 1
    assert len(result["blocked_queries"]) == 1
    assert result["blocked_queries"][0]["wait_seconds"] == 42
    assert result["blocked_queries"][0]["relation"] == "orders"
    assert len(result["lock_summary"]) == 2
    assert result["lock_summary"][0]["locktype"] == "relation"
    assert result["lock_summary"][0]["waiting"] == 1


def test_run_no_locks() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "blocked_query_count": 0,
        "blocked_queries": [],
        "lock_summary": [
            {"locktype": "relation", "granted": 3, "waiting": 0},
        ],
    }
    with patch("tools.postgresql_locks_tool.get_lock_status", return_value=fake_result):
        result = get_postgresql_lock_status(host="localhost", database="testdb")
    assert result["blocked_query_count"] == 0
    assert result["blocked_queries"] == []


def test_run_error_propagated() -> None:
    with patch(
        "tools.postgresql_locks_tool.get_lock_status",
        return_value={"source": "postgresql", "available": False, "error": "permission denied"},
    ):
        result = get_postgresql_lock_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "tools.postgresql_locks_tool.get_lock_status",
        return_value={
            "source": "postgresql",
            "available": True,
            "blocked_query_count": 0,
            "blocked_queries": [],
            "lock_summary": [],
        },
    ):
        result = get_postgresql_lock_status(host="localhost")
    assert "default_db_warning" in result
    assert "postgres" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "tools.postgresql_locks_tool.get_lock_status",
        return_value={
            "source": "postgresql",
            "available": True,
            "blocked_query_count": 0,
            "blocked_queries": [],
            "lock_summary": [],
        },
    ):
        result = get_postgresql_lock_status(host="localhost", database="mydb")
    assert "default_db_warning" not in result
