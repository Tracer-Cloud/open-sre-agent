"""Tests for SQSQueueAttributesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.SQSQueueAttributesTool import get_sqs_queue_attributes
from tests.tools.conftest import BaseToolContract


class TestSQSQueueAttributesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_sqs_queue_attributes.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_sqs_queue_attributes.__opensre_registered_tool__
    assert rt.name == "get_sqs_queue_attributes"
    assert rt.source == "sqs"


def test_is_available() -> None:
    rt = get_sqs_queue_attributes.__opensre_registered_tool__

    # Direct config
    assert rt.is_available({"sqs": {"connection_verified": True}}) is True
    assert rt.is_available({"sqs": {"_backend": object()}}) is True

    # AWS fallback integrations
    assert rt.is_available({"rds": {"connection_verified": True}}) is True
    assert rt.is_available({"ec2": {"connection_verified": True}}) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is True
    assert rt.is_available({"cloudwatch": {"connection_verified": True}}) is True
    assert rt.is_available({"cloudwatch": {}}) is True

    # Not available
    assert rt.is_available({}) is False
    assert rt.is_available({"sqs": {}}) is False


def test_extract_params() -> None:
    rt = get_sqs_queue_attributes.__opensre_registered_tool__
    sources = {
        "eks": {
            "region": "us-west-2",
            "_backend": "mock_eks_backend",
        }
    }
    params = rt.extract_params(sources)
    assert params["region"] == "us-west-2"
    assert params["aws_backend"] == "mock_eks_backend"


def test_run_happy_path() -> None:
    fake_list_res = {
        "success": True,
        "data": {"QueueUrls": ["https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"]},
    }

    fake_attr_res = {
        "success": True,
        "data": {
            "Attributes": {
                "QueueArn": "arn:aws:sqs:us-east-1:123456789012:test-queue",
                "ApproximateNumberOfMessages": "10",
                "ApproximateNumberOfMessagesNotVisible": "2",
                "ApproximateAgeOfOldestMessage": "3600",
                "VisibilityTimeout": "30",
                "RedrivePolicy": '{"deadLetterTargetArn":"arn:aws:sqs:us-east-1:123456789012:dlq","maxReceiveCount":"5"}',
                "FifoQueue": "false",
            }
        },
    }

    def mock_sdk_call(service_name, operation_name, parameters=None, region=None):
        if operation_name == "list_queues":
            return fake_list_res
        elif operation_name == "get_queue_attributes":
            assert (
                parameters["QueueUrl"]
                == "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"
            )
            return fake_attr_res
        return {"success": False, "error": "unknown operation"}

    with patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call", side_effect=mock_sdk_call):
        result = get_sqs_queue_attributes(queue_name_prefix="test-", region="us-east-1")

    assert result["available"] is True
    assert result["region"] == "us-east-1"
    assert result["total_queues"] == 1
    queue = result["queues"][0]
    assert queue["queue_name"] == "test-queue"
    assert queue["visible_messages"] == 10
    assert queue["in_flight_messages"] == 2
    assert queue["oldest_message_age_seconds"] == 3600
    assert queue["has_dlq"] is True
    assert queue["is_fifo"] is False


def test_run_aws_backend_override() -> None:
    mock_backend = MagicMock()
    mock_backend.get_sqs_queue_attributes.return_value = {
        "source": "sqs",
        "available": True,
        "queues": [{"queue_name": "custom-queue"}],
    }

    result = get_sqs_queue_attributes(aws_backend=mock_backend)
    assert result["available"] is True
    assert result["queues"][0]["queue_name"] == "custom-queue"
    mock_backend.get_sqs_queue_attributes.assert_called_once()


def test_run_list_queues_fail() -> None:
    fake_list_res = {"success": False, "error": "AccessDeniedException"}

    with patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call", return_value=fake_list_res):
        result = get_sqs_queue_attributes(region="us-east-1")

    assert result["available"] is False
    assert "AccessDeniedException" in result["error"]
    assert result["queues"] == []


def test_run_get_attributes_fail() -> None:
    fake_list_res = {
        "success": True,
        "data": {"QueueUrls": ["https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"]},
    }

    fake_attr_res = {"success": False, "error": "NoSuchQueueException"}

    def mock_sdk_call(service_name, operation_name, parameters=None, region=None):
        if operation_name == "list_queues":
            return fake_list_res
        elif operation_name == "get_queue_attributes":
            return fake_attr_res
        return {"success": False, "error": "unknown operation"}

    with patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call", side_effect=mock_sdk_call):
        result = get_sqs_queue_attributes(region="us-east-1")

    assert result["available"] is False
    assert "NoSuchQueueException" in result["error"]
    assert result["queues"] == []
