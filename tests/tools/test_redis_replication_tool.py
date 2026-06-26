"""Tests for RedisReplicationTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.RedisReplicationTool import get_redis_replication


class TestRedisReplicationToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_replication.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_replication.__opensre_registered_tool__
    assert rt.name == "get_redis_replication"
    assert rt.source == "redis"


def test_run_happy_path() -> None:
    fake_result = {"source": "redis", "available": True, "role": "master", "replicas": []}
    with patch("tools.RedisReplicationTool.get_replication", return_value=fake_result):
        result = get_redis_replication(host="localhost")
    assert result["role"] == "master"


def test_run_error_propagated() -> None:
    with patch(
        "tools.RedisReplicationTool.get_replication",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = get_redis_replication(host="invalid")
    assert "error" in result
