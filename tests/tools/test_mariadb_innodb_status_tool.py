"""Tests for MariaDBInnoDBStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.mariadb_innodb_status_tool import get_mariadb_innodb_status


class TestMariaDBInnoDBStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_innodb_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_innodb_status.__opensre_registered_tool__
    assert rt.name == "get_mariadb_innodb_status"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "innodb_status": "=====================================\nBUFFER POOL AND MEMORY\n=====================================",
    }
    with patch("tools.mariadb_innodb_status_tool.get_innodb_status", return_value=fake_result):
        result = get_mariadb_innodb_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert "innodb_status" in result


def test_run_error_propagated() -> None:
    with patch(
        "tools.mariadb_innodb_status_tool.get_innodb_status",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_innodb_status(host="invalid", database="test", username="user")
    assert "error" in result
