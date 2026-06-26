"""Tests for RedisServerInfoTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.redis_server_info_tool import get_redis_server_info


class TestRedisServerInfoToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_server_info.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_server_info.__opensre_registered_tool__
    assert rt.name == "get_redis_server_info"
    assert rt.source == "redis"


def test_run_happy_path() -> None:
    fake_result = {"source": "redis", "available": True, "version": "7.2.4"}
    with patch("tools.redis_server_info_tool.get_server_info", return_value=fake_result):
        result = get_redis_server_info(host="localhost")
    assert result["version"] == "7.2.4"
    assert result["available"] is True


def test_run_error_propagated() -> None:
    with patch(
        "tools.redis_server_info_tool.get_server_info",
        return_value={"source": "redis", "available": False, "error": "connection timeout"},
    ):
        result = get_redis_server_info(host="invalid")
    assert "error" in result
