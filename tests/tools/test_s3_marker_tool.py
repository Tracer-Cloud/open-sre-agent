"""Tests for S3MarkerTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.s3_marker_tool import check_s3_marker


class TestS3MarkerToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return check_s3_marker.__opensre_registered_tool__


def test_is_available_requires_bucket_and_prefix() -> None:
    rt = check_s3_marker.__opensre_registered_tool__
    assert rt.is_available({"s3": {"bucket": "b", "prefix": "p/"}}) is True
    assert rt.is_available({"s3_processed": {"bucket": "b"}}) is True
    assert rt.is_available({"s3": {"bucket": "b"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = check_s3_marker.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["bucket"] == "my-bucket"


def test_run_marker_exists() -> None:
    mock_result = MagicMock()
    mock_result.marker_exists = True
    mock_result.file_count = 5
    mock_result.files = ["_SUCCESS"]
    with patch(
        "tools.s3_marker_tool.check_s3_marker_presence",
        return_value=mock_result,
    ):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is True
    assert result["file_count"] == 5


def test_run_marker_missing() -> None:
    mock_result = MagicMock()
    mock_result.marker_exists = False
    mock_result.file_count = 0
    mock_result.files = []
    with patch(
        "tools.s3_marker_tool.check_s3_marker_presence",
        return_value=mock_result,
    ):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is False
