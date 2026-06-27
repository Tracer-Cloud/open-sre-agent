"""Tests for RedisKeyScanTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.redis_key_scan_tool import scan_redis_keys


class TestRedisKeyScanToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return scan_redis_keys.__opensre_registered_tool__


def test_metadata() -> None:
    rt = scan_redis_keys.__opensre_registered_tool__
    assert rt.name == "scan_redis_keys"
    assert rt.source == "redis"


def test_run_happy_path() -> None:
    fake_result = {"source": "redis", "available": True, "matched_keys": 3, "pattern": "session:*"}
    with patch("tools.redis_key_scan_tool.scan_keys", return_value=fake_result) as mock_fn:
        result = scan_redis_keys(host="localhost", pattern="session:*")
    assert result["matched_keys"] == 3
    assert mock_fn.call_args.kwargs["pattern"] == "session:*"


def test_run_error_propagated() -> None:
    with patch(
        "tools.redis_key_scan_tool.scan_keys",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = scan_redis_keys(host="invalid")
    assert "error" in result
