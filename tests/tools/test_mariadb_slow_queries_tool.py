"""Tests for MariaDBSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.mariadb_slow_queries_tool import get_mariadb_slow_queries


class TestMariaDBSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_slow_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_slow_queries.__opensre_registered_tool__
    assert rt.name == "get_mariadb_slow_queries"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "total_queries": 1,
        "queries": [{"digest_text": "SELECT ...", "count": 100, "avg_time_ms": 50.5}],
    }
    with patch("tools.mariadb_slow_queries_tool.get_slow_queries", return_value=fake_result):
        result = get_mariadb_slow_queries(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert result["total_queries"] == 1


def test_run_error_propagated() -> None:
    with patch(
        "tools.mariadb_slow_queries_tool.get_slow_queries",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_slow_queries(host="invalid", database="test", username="user")
    assert "error" in result
