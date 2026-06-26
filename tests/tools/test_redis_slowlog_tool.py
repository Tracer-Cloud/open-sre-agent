"""Tests for RedisSlowlogTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.RedisSlowlogTool import get_redis_slowlog


class TestRedisSlowlogToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_slowlog.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_slowlog.__opensre_registered_tool__
    assert rt.name == "get_redis_slowlog"
    assert rt.source == "redis"


def test_run_happy_path() -> None:
    fake_result = {"source": "redis", "available": True, "returned_entries": 1, "entries": [{}]}
    with patch("tools.RedisSlowlogTool.get_slowlog", return_value=fake_result) as mock_fn:
        result = get_redis_slowlog(host="localhost", limit=5)
    assert result["available"] is True
    assert mock_fn.call_args.kwargs["limit"] == 5


def test_run_error_propagated() -> None:
    with patch(
        "tools.RedisSlowlogTool.get_slowlog",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = get_redis_slowlog(host="invalid")
    assert "error" in result
