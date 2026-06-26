"""Tests for MongoDBProfilerTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.mongodb_profiler_tool import get_mongodb_profiler_data


class TestMongoDBProfilerToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_profiler_data.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_profiler_data.__opensre_registered_tool__
    assert rt.name == "get_mongodb_profiler_data"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {"queries": [{"op": "query", "millis": 500, "ns": "mydb.users"}]}
    with patch("tools.mongodb_profiler_tool.get_profiler_data", return_value=fake_result):
        result = get_mongodb_profiler_data(
            connection_string="mongodb://localhost:27017",
            database="my-db",
            threshold_ms=100,
        )
    assert "queries" in result


def test_run_error_propagated() -> None:
    with patch(
        "tools.mongodb_profiler_tool.get_profiler_data",
        return_value={"error": "profiling not enabled"},
    ):
        result = get_mongodb_profiler_data(connection_string="mongodb://localhost", database="mydb")
    assert "error" in result
