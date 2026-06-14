"""Tests for the SQS queue attributes tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.integrations.sqs import DEFAULT_SQS_MAX_QUEUES, sqs_extract_params, sqs_is_available
from app.tools.SQSQueueAttributesTool import get_sqs_queue_attributes
from tests.tools.conftest import BaseToolContract


class TestSQSQueueAttributesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_sqs_queue_attributes.__opensre_registered_tool__


def test_is_available_with_backend_source() -> None:
    assert sqs_is_available({"sqs": {"_backend": object()}}) is True


def test_is_available_with_env_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQS_REGION", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert sqs_is_available({}) is True


def test_is_available_false_when_no_source_or_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("SQS_REGION", raising=False)
    assert sqs_is_available({}) is False


def test_extract_params_clamps_max_queues_and_uses_region_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("SQS_REGION", "ap-south-1")

    backend = object()
    params = sqs_extract_params(
        {
            "sqs": {
                "queue_name_prefix": "payments-",
                "max_queues": 999,
                "_backend": backend,
            }
        }
    )

    assert params["queue_name_prefix"] == "payments-"
    assert params["max_queues"] == 100
    assert params["region"] == "ap-south-1"
    assert params["aws_backend"] is backend


def test_extract_params_uses_shared_default_for_invalid_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    params = sqs_extract_params({"sqs": {"max_queues": "invalid"}})
    assert params["max_queues"] == DEFAULT_SQS_MAX_QUEUES


# ---------------------------------------------------------------------------
# Fake backend for synthetic-mode short-circuit tests
# ---------------------------------------------------------------------------


class _FakeAWSBackend:
    """Minimal backend stand-in that records calls and returns canned responses."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get_sqs_queue_attributes(
        self,
        queue_name_prefix: str = "",
        max_queues: int = DEFAULT_SQS_MAX_QUEUES,
        region: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {"queue_name_prefix": queue_name_prefix, "max_queues": max_queues, "region": region}
        )
        return self.response


# ---------------------------------------------------------------------------
# list_queues success — happy path
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_get_sqs_queue_attributes_success(mock_call) -> None:
    mock_call.side_effect = [
        # list_queues
        {
            "success": True,
            "data": {
                "QueueUrls": [
                    "https://sqs.us-east-1.amazonaws.com/123456/payments-queue",
                ]
            },
        },
        # get_queue_attributes
        {
            "success": True,
            "data": {
                "Attributes": {
                    "ApproximateNumberOfMessages": "5",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateAgeOfOldestMessage": "120",
                    "VisibilityTimeout": "30",
                    "RedrivePolicy": '{"deadLetterTargetArn":"arn:aws:sqs:us-east-1:123456:payments-dlq","maxReceiveCount":"3"}',
                    "FifoQueue": "false",
                    "ContentBasedDeduplication": "false",
                }
            },
        },
    ]

    result = get_sqs_queue_attributes(queue_name_prefix="payments", region="us-east-1")

    assert result["available"] is True
    assert result["total_queues"] == 1
    assert result["truncated"] is False
    assert result["error"] is None

    q = result["queues"][0]
    assert q["name"] == "payments-queue"
    assert q["visible_count"] == 5
    assert q["in_flight_count"] == 0
    assert q["oldest_message_age_seconds"] == 120
    assert q["visibility_timeout_seconds"] == 30
    assert q["has_dlq"] is True
    assert q["redrive_policy"]["maxReceiveCount"] == "3"
    assert q["is_fifo"] is False


# ---------------------------------------------------------------------------
# Empty queue list — no QueueUrls returned
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_get_sqs_queue_attributes_no_queues(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"QueueUrls": []}}

    result = get_sqs_queue_attributes(queue_name_prefix="nonexistent")

    assert result["available"] is True
    assert result["total_queues"] == 0
    assert result["truncated"] is False
    assert result["queues"] == []


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_get_sqs_queue_attributes_marks_truncated_when_next_token_present(mock_call) -> None:
    mock_call.side_effect = [
        {
            "success": True,
            "data": {
                "QueueUrls": ["https://sqs.us-east-1.amazonaws.com/123456/queue-a"],
                "NextToken": "token-1",
            },
        },
        {
            "success": True,
            "data": {
                "Attributes": {
                    "ApproximateNumberOfMessages": "1",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateAgeOfOldestMessage": "5",
                    "VisibilityTimeout": "30",
                    "FifoQueue": "false",
                }
            },
        },
    ]

    result = get_sqs_queue_attributes(max_queues=1)

    assert result["available"] is True
    assert result["total_queues"] == 1
    assert result["truncated"] is True


# ---------------------------------------------------------------------------
# list_queues failure → available: False
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_get_sqs_queue_attributes_list_failure(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "AccessDenied"}

    result = get_sqs_queue_attributes()

    assert result["available"] is False
    assert "Failed to list SQS queues" in result["error"]


# ---------------------------------------------------------------------------
# get_queue_attributes failure for one queue — partial result, not fatal
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_get_sqs_queue_attributes_partial_attr_failure(mock_call) -> None:
    mock_call.side_effect = [
        {
            "success": True,
            "data": {
                "QueueUrls": [
                    "https://sqs.us-east-1.amazonaws.com/123456/queue-a",
                    "https://sqs.us-east-1.amazonaws.com/123456/queue-b",
                ]
            },
        },
        # queue-a attributes fail
        {"success": False, "error": "AccessDenied"},
        # queue-b attributes succeed
        {
            "success": True,
            "data": {
                "Attributes": {
                    "ApproximateNumberOfMessages": "2",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateAgeOfOldestMessage": "10",
                    "VisibilityTimeout": "60",
                    "FifoQueue": "false",
                }
            },
        },
    ]

    result = get_sqs_queue_attributes()

    assert result["available"] is True
    assert result["total_queues"] == 2
    assert "attributes_error" in result["queues"][0]
    assert result["queues"][1]["visible_count"] == 2


# ---------------------------------------------------------------------------
# Poison-pill fixture: visible=0, in-flight=3, no DLQ, VisibilityTimeout=30
# This is the exact scenario from the incident described in the issue.
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_poison_pill_fixture(mock_call) -> None:
    mock_call.side_effect = [
        {
            "success": True,
            "data": {
                "QueueUrls": [
                    "https://sqs.us-east-1.amazonaws.com/123456/payments-queue",
                ]
            },
        },
        {
            "success": True,
            "data": {
                "Attributes": {
                    "ApproximateNumberOfMessages": "0",
                    "ApproximateNumberOfMessagesNotVisible": "3",
                    "ApproximateAgeOfOldestMessage": "3600",
                    "VisibilityTimeout": "30",
                    # No RedrivePolicy key — no DLQ configured
                    "FifoQueue": "false",
                }
            },
        },
    ]

    result = get_sqs_queue_attributes(queue_name_prefix="payments")

    assert result["available"] is True
    q = result["queues"][0]
    # All consumers stuck holding messages
    assert q["visible_count"] == 0
    assert q["in_flight_count"] == 3
    # Message is very old — stuck in flight
    assert q["oldest_message_age_seconds"] == 3600
    # Short visibility timeout means re-delivery to already-stuck consumers
    assert q["visibility_timeout_seconds"] == 30
    # No DLQ — messages cycle forever
    assert q["has_dlq"] is False
    assert q["redrive_policy"] is None


# ---------------------------------------------------------------------------
# FIFO queue detection
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_fifo_queue_detected(mock_call) -> None:
    mock_call.side_effect = [
        {
            "success": True,
            "data": {
                "QueueUrls": [
                    "https://sqs.us-east-1.amazonaws.com/123456/orders-queue.fifo",
                ]
            },
        },
        {
            "success": True,
            "data": {
                "Attributes": {
                    "ApproximateNumberOfMessages": "0",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateAgeOfOldestMessage": "0",
                    "VisibilityTimeout": "30",
                    "FifoQueue": "true",
                    "ContentBasedDeduplication": "true",
                }
            },
        },
    ]

    result = get_sqs_queue_attributes()
    q = result["queues"][0]
    assert q["is_fifo"] is True
    assert q["content_based_deduplication"] is True


# ---------------------------------------------------------------------------
# Synthetic-mode: aws_backend short-circuit — execute_aws_sdk_call must NOT fire
# ---------------------------------------------------------------------------


@patch("app.tools.SQSQueueAttributesTool.execute_aws_sdk_call")
def test_short_circuits_to_aws_backend(mock_call) -> None:
    canned = {
        "source": "sqs",
        "available": True,
        "queue_name_prefix": "payments",
        "region": "us-east-1",
        "total_queues": 1,
        "queues": [
            {
                "name": "payments-queue",
                "url": "https://sqs.us-east-1.amazonaws.com/123456/payments-queue",
                "visible_count": 0,
                "in_flight_count": 3,
                "oldest_message_age_seconds": 3600,
                "visibility_timeout_seconds": 30,
                "has_dlq": False,
                "redrive_policy": None,
                "is_fifo": False,
                "content_based_deduplication": False,
            }
        ],
        "error": None,
    }
    backend = _FakeAWSBackend(canned)

    result = get_sqs_queue_attributes(
        queue_name_prefix="payments",
        max_queues=DEFAULT_SQS_MAX_QUEUES,
        region="us-east-1",
        aws_backend=backend,
    )

    mock_call.assert_not_called()
    assert backend.calls == [
        {
            "queue_name_prefix": "payments",
            "max_queues": DEFAULT_SQS_MAX_QUEUES,
            "region": "us-east-1",
        }
    ]
    assert result["total_queues"] == 1
    assert result["queues"][0]["has_dlq"] is False
