"""Tests for AzureSQLCurrentQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.azure_sql_current_queries_tool import get_azure_sql_current_queries


class TestAzureSQLCurrentQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_azure_sql_current_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_azure_sql_current_queries.__opensre_registered_tool__
    assert rt.name == "get_azure_sql_current_queries"
    assert rt.source == "azure_sql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "azure_sql",
        "available": True,
        "threshold_seconds": 1,
        "total_queries": 1,
        "queries": [
            {
                "session_id": 55,
                "login_name": "admin",
                "status": "running",
                "duration_seconds": 5,
                "query_text": "SELECT * FROM orders",
            }
        ],
    }
    with patch(
        "tools.azure_sql_current_queries_tool.get_current_queries",
        return_value=fake_result,
    ):
        result = get_azure_sql_current_queries(
            server="myserver.database.windows.net", database="testdb"
        )
    assert result["total_queries"] == 1
    assert result["queries"][0]["session_id"] == 55


def test_run_error_propagated() -> None:
    with patch(
        "tools.azure_sql_current_queries_tool.get_current_queries",
        return_value={"source": "azure_sql", "available": False, "error": "timeout"},
    ):
        result = get_azure_sql_current_queries(server="invalid", database="testdb")
    assert result["available"] is False


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "tools.azure_sql_current_queries_tool.get_current_queries",
        return_value={"source": "azure_sql", "available": True, "queries": []},
    ):
        result = get_azure_sql_current_queries(server="myserver.database.windows.net")
    assert "default_db_warning" in result
    assert "master" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "tools.azure_sql_current_queries_tool.get_current_queries",
        return_value={"source": "azure_sql", "available": True, "queries": []},
    ):
        result = get_azure_sql_current_queries(
            server="myserver.database.windows.net", database="mydb"
        )
    assert "default_db_warning" not in result
