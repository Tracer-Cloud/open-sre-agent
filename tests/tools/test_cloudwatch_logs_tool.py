"""Tests for CloudWatchLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.CloudWatchLogsTool import get_cloudwatch_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestCloudWatchLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_cloudwatch_logs.__opensre_registered_tool__


def test_is_available_requires_log_group() -> None:
    rt = get_cloudwatch_logs.__opensre_registered_tool__
    assert rt.is_available({"cloudwatch": {"log_group": "/aws/lambda/fn"}}) is True
    assert rt.is_available({"cloudwatch": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_cloudwatch_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["log_group"] == "/aws/lambda/my-function"
    assert params["log_stream"] == "2024/01/01/[$LATEST]abc123"
    assert params["filter_pattern"] == "req-123"
    assert params["limit"] == 100


def test_run_returns_error_when_no_log_group() -> None:
    result = get_cloudwatch_logs(log_group="")
    assert "error" in result


def test_run_with_filter_pattern_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.filter_log_events.return_value = {
        "events": [{"message": "Error: something failed", "timestamp": 1000}]
    }
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", filter_pattern="Error")
    assert result["found"] is True
    assert result["event_count"] == 1
    assert "Error: something failed" in result["error_logs"]


def test_run_with_filter_pattern_no_events() -> None:
    mock_client = MagicMock()
    mock_client.filter_log_events.return_value = {"events": []}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", filter_pattern="Error")
    assert result["found"] is False
    assert "filter_pattern" in result


def test_run_auto_discovers_log_stream() -> None:
    mock_client = MagicMock()
    mock_client.describe_log_streams.return_value = {"logStreams": [{"logStreamName": "stream-1"}]}
    mock_client.get_log_events.return_value = {"events": [{"message": "hello"}]}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group")
    assert result["found"] is True
    assert result["log_stream"] == "stream-1"


def test_run_no_streams_found() -> None:
    mock_client = MagicMock()
    mock_client.describe_log_streams.return_value = {"logStreams": []}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group")
    assert result["found"] is False


def test_run_with_explicit_log_stream() -> None:
    mock_client = MagicMock()
    mock_client.get_log_events.return_value = {"events": [{"message": "msg1"}, {"message": "msg2"}]}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", log_stream="stream-x")
    assert result["found"] is True
    assert result["event_count"] == 2


def test_run_handles_boto3_exception() -> None:
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.side_effect = Exception("AWS error")
        result = get_cloudwatch_logs(log_group="/my/group")
    assert "error" in result


def test_run_applies_log_compaction() -> None:
    """Regression: deduplication, error_taxonomy, and total_raw_logs are present."""
    # 10 identical error events — compaction should dedupe to at most 20 error slots
    events = [{"message": f"Error: timeout after 30s", "timestamp": 1000 + i} for i in range(10)]
    mock_client = MagicMock()
    mock_client.get_log_events.return_value = {"events": events}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", log_stream="stream-x")
    assert result["found"] is True
    assert result["event_count"] == 10
    assert result["total_raw_logs"] == 10
    # error_logs should be deduplicated list of message strings
    assert isinstance(result["error_logs"], list)
    assert len(result["error_logs"]) <= 20
    # error_taxonomy should be a dict with expected keys
    assert "error_taxonomy" in result
    assert isinstance(result["error_taxonomy"], dict)
    assert "error_taxonomy" in result["error_taxonomy"]


def test_run_compaction_limits_error_logs_to_50() -> None:
    """Error logs capped at 50 after deduplication."""
    events = [
        {"message": f"Error: fail {i}", "timestamp": 2000 + i}
        for i in range(60)
    ]
    mock_client = MagicMock()
    mock_client.get_log_events.return_value = {"events": events}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", log_stream="stream-x")
    # All 60 are errors (contain 'error')
    assert result["total_raw_logs"] == 60
    # compacted to max 50
    assert len(result["error_logs"]) <= 50


def test_run_error_taxonomy_classifies_error_types() -> None:
    """Error taxonomy groups logs by error type."""
    events = [
        {"message": "error: connection timeout — server did not respond", "timestamp": 3000},
        {"message": "exception: connection timeout — connection refused", "timestamp": 3001},
        {"message": "error: permission denied — access denied for user", "timestamp": 3002},
    ]
    mock_client = MagicMock()
    mock_client.get_log_events.return_value = {"events": events}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", log_stream="stream-x")
    taxonomy = result["error_taxonomy"]
    assert taxonomy["total_logs_fetched"] == 3
    assert taxonomy["distinct_error_types"] == 2
    error_types = {bucket["error_type"] for bucket in taxonomy["error_taxonomy"]}
    assert "ConnectionTimeout" in error_types
    assert "PermissionDenied" in error_types
