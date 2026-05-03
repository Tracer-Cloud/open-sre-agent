"""Unit tests for the generic AWS SDK client safety boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from app.services.aws_sdk_client import (
    MAX_LIST_ITEMS,
    _is_operation_allowed,
    _sanitize_response,
    execute_aws_sdk_call,
)


def test_is_operation_allowed_allows_readonly_operations() -> None:
    allowed, reason = _is_operation_allowed("describe_instances")
    assert allowed is True
    assert "allowed" in reason.lower()


@pytest.mark.parametrize(
    "operation_name",
    [
        "describe_instances",
        "get_caller_identity",
        "list_buckets",
        "head_bucket",
        "batch_get_item",
        "query",
        "scan",
    ],
)
def test_is_operation_allowed_allows_expected_patterns(operation_name: str) -> None:
    allowed, reason = _is_operation_allowed(operation_name)
    assert allowed is True
    assert "allowed" in reason.lower()


@pytest.mark.parametrize(
    "operation_name",
    [
        "delete_table",
        "remove_bucket",
        "update_item",
        "put_object",
        "create_bucket",
        "terminate_instances",
    ],
)
def test_is_operation_allowed_blocks_destructive_patterns(operation_name: str) -> None:
    allowed, reason = _is_operation_allowed(operation_name)
    assert allowed is False
    assert "blocked pattern" in reason


def test_is_operation_allowed_rejects_unknown_operation() -> None:
    allowed, reason = _is_operation_allowed("do_thing")
    assert allowed is False
    assert "does not match any allowed patterns" in reason


def test_sanitize_response_handles_datetime_bytes_and_metadata() -> None:
    data = {
        "StartedAt": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "Blob": b"\x00\x01",
        "ResponseMetadata": {"RequestId": "abc"},
        "Nested": {"ok": True},
    }
    sanitized = _sanitize_response(data)
    assert sanitized["StartedAt"] == "2026-01-02T03:04:05+00:00"
    assert sanitized["Blob"] == "<binary data: 2 bytes>"
    assert "ResponseMetadata" not in sanitized
    assert sanitized["Nested"]["ok"] is True


def test_sanitize_response_truncates_large_lists() -> None:
    data = list(range(MAX_LIST_ITEMS + 5))
    sanitized = _sanitize_response(data)
    assert isinstance(sanitized, list)
    assert len(sanitized) == MAX_LIST_ITEMS + 1
    assert "more items truncated" in str(sanitized[-1])


def test_sanitize_response_limits_depth() -> None:
    data: dict[str, object] = {}
    cursor: dict[str, object] = data
    for i in range(20):
        next_cursor: dict[str, object] = {}
        cursor[f"level_{i}"] = next_cursor
        cursor = next_cursor

    sanitized = _sanitize_response(data)
    # Walk until we hit the sentinel truncation.
    cursor_any: object = sanitized
    for i in range(20):
        if cursor_any == "... (max depth reached)":
            break
        assert isinstance(cursor_any, dict)
        cursor_any = cursor_any[f"level_{i}"]
    assert cursor_any == "... (max depth reached)"


def test_execute_aws_sdk_call_requires_service_and_operation() -> None:
    result = execute_aws_sdk_call(service_name="", operation_name="")
    assert result["success"] is False
    assert "required" in result["error"]


def test_execute_aws_sdk_call_blocks_disallowed_operation() -> None:
    result = execute_aws_sdk_call(service_name="ec2", operation_name="delete_vpc")
    assert result["success"] is False
    assert result["metadata"]["validation_failed"] is True
    assert "not allowed" in result["error"].lower()


def test_execute_aws_sdk_call_missing_operation_returns_error() -> None:
    client = SimpleNamespace(meta=SimpleNamespace(region_name="us-east-1"))
    with patch("app.services.aws_sdk_client.boto3.client", return_value=client):
        result = execute_aws_sdk_call(service_name="ec2", operation_name="describe_instances")
    assert result["success"] is False
    assert "not found" in result["error"].lower()
    assert "available_operations" in result["metadata"]


def test_execute_aws_sdk_call_happy_path_sanitizes_response() -> None:
    client = MagicMock()
    client.meta.region_name = "us-east-1"
    client.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"InstanceId": "i-123"}]}],
        "ResponseMetadata": {"RequestId": "abc"},
    }
    with patch("app.services.aws_sdk_client.boto3.client", return_value=client):
        result = execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_instances",
            parameters={"InstanceIds": ["i-123"]},
        )

    assert result["success"] is True
    assert result["data"]["Reservations"][0]["Instances"][0]["InstanceId"] == "i-123"
    assert "ResponseMetadata" not in result["data"]
    assert result["metadata"]["region"] == "us-east-1"
    assert result["metadata"]["parameters_provided"] is True
    client.describe_instances.assert_called_once_with(InstanceIds=["i-123"])


def test_execute_aws_sdk_call_passes_region_to_boto3_client() -> None:
    client = MagicMock()
    client.meta.region_name = "us-west-2"
    client.describe_instances.return_value = {}

    with patch("app.services.aws_sdk_client.boto3.client", return_value=client) as mock_boto3:
        result = execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_instances",
            region="us-west-2",
        )

    assert result["success"] is True
    mock_boto3.assert_called_once_with("ec2", region_name="us-west-2")


def test_execute_aws_sdk_call_no_credentials_error() -> None:
    with patch("app.services.aws_sdk_client.boto3.client", side_effect=NoCredentialsError()):
        result = execute_aws_sdk_call(service_name="ec2", operation_name="describe_instances")
    assert result["success"] is False
    assert result["metadata"]["error_type"] == "credentials"


def test_execute_aws_sdk_call_param_validation_error() -> None:
    client = MagicMock()
    client.meta.region_name = "us-east-1"
    client.describe_instances.side_effect = ParamValidationError(report="bad params")
    with patch("app.services.aws_sdk_client.boto3.client", return_value=client):
        result = execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_instances",
            parameters={"Bad": "Value"},
        )
    assert result["success"] is False
    assert result["metadata"]["error_type"] == "validation"
    assert result["metadata"]["parameters"] == {"Bad": "Value"}


def test_execute_aws_sdk_call_client_error_formats_message() -> None:
    client = MagicMock()
    client.meta.region_name = "us-east-1"
    error_response = {
        "Error": {"Code": "AccessDeniedException", "Message": "nope"},
        "ResponseMetadata": {"HTTPStatusCode": 403},
    }
    client.describe_instances.side_effect = ClientError(error_response, "DescribeInstances")
    with patch("app.services.aws_sdk_client.boto3.client", return_value=client):
        result = execute_aws_sdk_call(service_name="ec2", operation_name="describe_instances")
    assert result["success"] is False
    assert "AccessDeniedException" in result["error"]
    assert "nope" in result["error"]
    assert result["metadata"]["error_type"] == "client_error"
    assert result["metadata"]["status_code"] == 403


def test_execute_aws_sdk_call_unexpected_exception_sets_error_type() -> None:
    client = MagicMock()
    client.meta.region_name = "us-east-1"
    client.describe_instances.side_effect = RuntimeError("boom")

    with patch("app.services.aws_sdk_client.boto3.client", return_value=client):
        result = execute_aws_sdk_call(service_name="ec2", operation_name="describe_instances")

    assert result["success"] is False
    assert result["metadata"]["error_type"] == "unexpected"
