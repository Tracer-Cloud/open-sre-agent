"""Tests for RedisListDepthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.RedisListDepthTool import get_redis_list_depth


class TestRedisListDepthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_list_depth.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_list_depth.__opensre_registered_tool__
    assert rt.name == "get_redis_list_depth"
    assert rt.source == "redis"


def test_key_is_required_and_host_is_injected() -> None:
    rt = get_redis_list_depth.__opensre_registered_tool__
    # `key` is supplied by the model; `host` is injected from the resolved config.
    assert rt.public_input_schema["required"] == ["key"]
    assert "host" not in rt.public_input_schema["properties"]
    assert "host" in rt.injected_params


def test_run_happy_path_forwards_key_and_sample_args() -> None:
    fake = {"source": "redis", "available": True, "depth": 7}
    with patch("tools.RedisListDepthTool.get_list_depth", return_value=fake) as mock_fn:
        result = get_redis_list_depth(key="jobs", host="localhost", head=2, tail=1)
    assert result["depth"] == 7
    assert mock_fn.call_args.kwargs == {"key": "jobs", "head": 2, "tail": 1}


def test_run_error_propagated() -> None:
    with patch(
        "tools.RedisListDepthTool.get_list_depth",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = get_redis_list_depth(key="jobs", host="invalid")
    assert result["available"] is False
