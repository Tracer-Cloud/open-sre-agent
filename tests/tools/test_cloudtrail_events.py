"""Tests for CloudTrailEventsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.CloudTrailEventsTool import lookup_cloudtrail_events
from tests.tools.conftest import BaseToolContract


class TestCloudTrailEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return lookup_cloudtrail_events.__opensre_registered_tool__


def test_metadata() -> None:
    rt = lookup_cloudtrail_events.__opensre_registered_tool__
    assert rt.name == "lookup_cloudtrail_events"
    assert rt.source == "cloudtrail"


def test_run_happy_path() -> None:
    fake_res = {
        "success": True,
        "data": {
            "Events": [
                {
                    "EventId": "evt-123",
                    "EventName": "DeleteQueue",
                    "EventTime": "2024-01-15 10:30:00",
                    "EventSource": "sqs.amazonaws.com",
                    "Username": "admin-role",
                    "Resources": [
                        {"ResourceType": "AWS::SQS::Queue", "ResourceName": "test-queue"}
                    ],
                }
            ]
        },
    }

    with patch(
        "app.tools.CloudTrailEventsTool.execute_aws_sdk_call", return_value=fake_res
    ) as mock_call:
        result = lookup_cloudtrail_events(
            resource_name="test-queue",
            event_source="sqs.amazonaws.com",
            username="admin-role",
            duration_minutes=30,
            region="us-east-1",
        )

    assert result["available"] is True
    assert result["region"] == "us-east-1"
    assert result["total_events"] == 1
    event = result["events"][0]
    assert event["event_id"] == "evt-123"
    assert event["event_name"] == "DeleteQueue"
    assert event["username"] == "admin-role"
    assert event["resources"][0]["name"] == "test-queue"

    mock_call.assert_called_once()
    called_args, called_kwargs = mock_call.call_args
    params = called_kwargs.get("parameters") or called_args[2]
    assert "StartTime" in params
    assert "EndTime" in params
    assert len(params["LookupAttributes"]) == 3
    assert {"AttributeKey": "ResourceName", "AttributeValue": "test-queue"} in params[
        "LookupAttributes"
    ]


def test_run_aws_backend_override() -> None:
    mock_backend = MagicMock()
    mock_backend.lookup_events.return_value = {
        "source": "cloudtrail",
        "available": True,
        "events": [{"event_id": "custom-evt"}],
    }

    result = lookup_cloudtrail_events(aws_backend=mock_backend)
    assert result["available"] is True
    assert result["events"][0]["event_id"] == "custom-evt"
    mock_backend.lookup_events.assert_called_once()


def test_run_lookup_fail() -> None:
    fake_res = {"success": False, "error": "AccessDeniedException"}

    with patch("app.tools.CloudTrailEventsTool.execute_aws_sdk_call", return_value=fake_res):
        result = lookup_cloudtrail_events(region="us-east-1")

    assert result["available"] is False
    assert "AccessDeniedException" in result["error"]
    assert result["events"] == []
