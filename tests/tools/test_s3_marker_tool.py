"""Tests for S3MarkerTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.s3.tools.s3_marker_tool import check_s3_marker
from tests.tools.conftest import BaseToolContract, mock_agent_state


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
    fake_data = {
        "objects": [{"key": "data/_SUCCESS"}, {"key": "data/part-0"}],
        "count": 2,
    }
    with patch(
        "integrations.s3.tools.s3_marker_tool.list_objects",
        return_value={"success": True, "data": fake_data},
    ):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is True
    assert result["file_count"] == 2


def test_run_marker_missing() -> None:
    fake_data = {"objects": [], "count": 0}
    with patch(
        "integrations.s3.tools.s3_marker_tool.list_objects",
        return_value={"success": True, "data": fake_data},
    ):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is False
    assert "error" not in result


def test_run_listing_error_does_not_look_like_missing_marker() -> None:
    with patch(
        "integrations.s3.tools.s3_marker_tool.list_objects",
        return_value={"success": False, "error": "boto3 not available"},
    ):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result == {"error": "boto3 not available", "bucket": "b", "prefix": "data/"}
    assert "marker_exists" not in result
