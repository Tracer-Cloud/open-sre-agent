"""Tests for RedisLatencyDoctorTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract
from tools.redis_latency_doctor_tool import get_redis_latency_doctor


class TestRedisLatencyDoctorToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_latency_doctor.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_latency_doctor.__opensre_registered_tool__
    assert rt.name == "get_redis_latency_doctor"
    assert rt.source == "redis"


def test_run_happy_path_forwards_event_and_history_limit() -> None:
    fake = {"source": "redis", "available": True, "report": "ok", "monitoring_active": True}
    with patch("tools.redis_latency_doctor_tool.get_latency_doctor", return_value=fake) as mock_fn:
        result = get_redis_latency_doctor(host="localhost", event="command", history_limit=10)
    assert result["available"] is True
    assert mock_fn.call_args.kwargs["event"] == "command"
    assert mock_fn.call_args.kwargs["history_limit"] == 10


def test_run_error_propagated() -> None:
    with patch(
        "tools.redis_latency_doctor_tool.get_latency_doctor",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = get_redis_latency_doctor(host="invalid")
    assert result["available"] is False
