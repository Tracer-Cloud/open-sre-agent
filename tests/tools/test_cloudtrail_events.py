"""Tests for CloudTrailEventsTool (function-based, @tool decorated).

Covers the acceptance criteria: schema shape, availability (mirrors an AWS
tool / planner-selectable), and extraction, plus runtime behavior — filter
priority, the time-window helper, response shaping, and the synthetic
``aws_backend`` short-circuit that keeps scenario runs off real AWS.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from integrations.cloudtrail import (
    DEFAULT_CLOUDTRAIL_REGION,
    cloudtrail_extract_params,
    cloudtrail_is_available,
)
from integrations.cloudtrail.tools.cloudtrail_events_tool import lookup_cloudtrail_events
from tests.tools.conftest import BaseToolContract

_RT = lookup_cloudtrail_events.__opensre_registered_tool__


class _FakeAWSBackend:
    """Minimal AWSBackend stand-in that records lookup_events calls."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def lookup_events(
        self,
        lookup_attributes: list[dict[str, str]],
        duration_minutes: int = 60,
        max_results: int = 50,
        region: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "lookup_attributes": lookup_attributes,
                "duration_minutes": duration_minutes,
                "max_results": max_results,
                "region": region,
            }
        )
        return self.response


class TestCloudTrailEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _RT


# ─────────────────────────────────────────────────────────────────────────────
# Discovery — the tool is auto-registered on the investigation surface
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_discovered_on_investigation_surface() -> None:
    from tools.registry import get_registered_tools

    names = {t.name for t in get_registered_tools("investigation")}
    assert "lookup_cloudtrail_events" in names


def test_cloudtrail_prioritized_but_not_auto_seeded_for_aws_alerts() -> None:
    """CloudTrail is prioritized for AWS alerts but left for the planner to call.

    It is intentionally NOT in the auto-seed map: an unscoped, account-wide
    lookup on every cloudwatch/eks/alertmanager alert would be noisy and press
    CloudTrail's low lookup rate limit. The prompt prioritization map nudges the
    planner to reach for it once it has a concrete resource/principal/time target.
    """
    from core.domain.alerts.alert_source import ALERT_SOURCE_ROUTING

    for alert_source in ("cloudwatch", "eks", "alertmanager"):
        routing = ALERT_SOURCE_ROUTING[alert_source]
        assert "cloudtrail" not in routing.seed_tool_sources
        assert "cloudtrail" in routing.relevance_tool_sources


# ─────────────────────────────────────────────────────────────────────────────
# Schema shape
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_shape() -> None:
    assert _RT.name == "lookup_cloudtrail_events"
    assert _RT.source == "cloudtrail"
    schema = _RT.input_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    # The three forensic filters + the window/region knobs are all present.
    for field in ("resource_name", "event_source", "username", "region", "duration_minutes"):
        assert field in props
    # All filters are optional — the tool is account-wide and selectable without one.
    assert schema["required"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Availability — mirrors an AWS tool (gates on the account-level "aws" source)
# ─────────────────────────────────────────────────────────────────────────────


def test_is_available_on_verified_aws() -> None:
    assert cloudtrail_is_available({"aws": {"connection_verified": True}}) is True


def test_is_available_on_role_or_credentials() -> None:
    assert cloudtrail_is_available({"aws": {"role_arn": "arn:aws:iam::1:role/r"}}) is True
    assert cloudtrail_is_available({"aws": {"credentials": {"access_key_id": "AKIA"}}}) is True


def test_is_available_on_injected_backend() -> None:
    # The synthetic harness injects the fixture backend as "ec2_backend" on the
    # "aws" source (see tests/synthetic/rds_postgres/run_suite.py).
    assert cloudtrail_is_available({"aws": {"ec2_backend": object()}}) is True


def test_not_available_without_aws() -> None:
    assert cloudtrail_is_available({}) is False
    assert cloudtrail_is_available({"aws": {}}) is False


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_params_region_from_source() -> None:
    params = cloudtrail_extract_params(
        {"aws": {"connection_verified": True, "region": "eu-west-1"}}
    )
    assert params["region"] == "eu-west-1"
    assert params["aws_backend"] is None


def test_extract_params_defaults_region(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    params = cloudtrail_extract_params({"aws": {"connection_verified": True}})
    assert params["region"] == DEFAULT_CLOUDTRAIL_REGION


def test_extract_params_forwards_backend() -> None:
    backend = object()
    # Harness-shaped: the backend handle lives under "ec2_backend" on "aws".
    params = cloudtrail_extract_params({"aws": {"ec2_backend": backend}})
    assert params["aws_backend"] is backend


# ─────────────────────────────────────────────────────────────────────────────
# Runtime behavior
# ─────────────────────────────────────────────────────────────────────────────


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_lookup_success_and_shaping(mock_call) -> None:
    detail = json.dumps(
        {"awsRegion": "us-east-1", "sourceIPAddress": "1.2.3.4", "errorCode": "AccessDenied"}
    )
    mock_call.return_value = {
        "success": True,
        "data": {
            "Events": [
                {
                    "EventId": "evt-1",
                    "EventName": "DeleteSecurityGroup",
                    "EventTime": "2026-05-05T12:00:00Z",
                    "EventSource": "ec2.amazonaws.com",
                    "Username": "alice",
                    "ReadOnly": "false",
                    "AccessKeyId": "AKIAEXAMPLE",
                    "Resources": [
                        {"ResourceType": "AWS::EC2::SecurityGroup", "ResourceName": "sg-1"}
                    ],
                    "CloudTrailEvent": detail,
                }
            ]
        },
    }

    result = lookup_cloudtrail_events(event_source="ec2.amazonaws.com", duration_minutes=120)

    assert result["available"] is True
    assert result["total_events"] == 1
    event = result["events"][0]
    assert event["event_name"] == "DeleteSecurityGroup"
    assert event["username"] == "alice"
    # "false" -> real bool False (not the truthy string "false")
    assert event["read_only"] is False
    assert event["resources"] == [{"type": "AWS::EC2::SecurityGroup", "name": "sg-1"}]
    assert event["aws_region"] == "us-east-1"
    assert event["source_ip_address"] == "1.2.3.4"
    assert event["error_code"] == "AccessDenied"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_filter_priority_prefers_resource_name(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(
        resource_name="sg-1",
        username="alice",
        event_source="ec2.amazonaws.com",
    )

    sent = mock_call.call_args.kwargs["parameters"]
    assert sent["LookupAttributes"] == [{"AttributeKey": "ResourceName", "AttributeValue": "sg-1"}]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_filter_priority_username_over_event_source(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(username="alice", event_source="iam.amazonaws.com")

    sent = mock_call.call_args.kwargs["parameters"]
    assert sent["LookupAttributes"] == [{"AttributeKey": "Username", "AttributeValue": "alice"}]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_no_filter_omits_lookup_attributes(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events()

    sent = mock_call.call_args.kwargs["parameters"]
    assert "LookupAttributes" not in sent
    # The time window is always sent.
    assert "StartTime" in sent and "EndTime" in sent
    assert result["filter"] is None


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_duration_clamped_and_window_built(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events(duration_minutes=10**9)

    sent = mock_call.call_args.kwargs["parameters"]
    # End is after start; duration clamped to the 90-day max.
    assert sent["EndTime"] > sent["StartTime"]
    assert result["duration_minutes"] == 90 * 24 * 60


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_max_results_clamped(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(max_results=999)

    assert mock_call.call_args.kwargs["parameters"]["MaxResults"] == 50


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_truncated_when_next_token_present(mock_call) -> None:
    mock_call.return_value = {
        "success": True,
        "data": {"Events": [], "NextToken": "tok-abc"},
    }

    result = lookup_cloudtrail_events()

    # A NextToken means more events exist beyond this page — surface it.
    assert result["truncated"] is True
    assert result["next_token"] == "tok-abc"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_not_truncated_without_next_token(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events()

    assert result["truncated"] is False
    assert result["next_token"] is None


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_next_token_forwarded_to_api(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(next_token="tok-page-2")

    assert mock_call.call_args.kwargs["parameters"]["NextToken"] == "tok-page-2"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_lookup_failure(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "ThrottlingException"}

    result = lookup_cloudtrail_events()

    assert result["available"] is False
    assert result["error"] == "Failed to look up CloudTrail events. Check server logs for details."


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_short_circuits_to_aws_backend(mock_call) -> None:
    backend = _FakeAWSBackend(
        response={
            "source": "cloudtrail",
            "available": True,
            "total_events": 0,
            "events": [],
            "error": None,
        }
    )

    result = lookup_cloudtrail_events(
        resource_name="sg-1",
        region="us-east-1",
        duration_minutes=30,
        max_results=25,
        aws_backend=backend,
    )

    mock_call.assert_not_called()
    assert result["available"] is True
    assert backend.calls == [
        {
            "lookup_attributes": [{"AttributeKey": "ResourceName", "AttributeValue": "sg-1"}],
            "duration_minutes": 30,
            "max_results": 25,
            "region": "us-east-1",
        }
    ]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_harness_shaped_aws_source_short_circuits_off_real_aws(mock_call) -> None:
    """Regression: the synthetic harness injects the backend as aws['ec2_backend'].

    Resolving via cloudtrail_extract_params (not injecting aws_backend directly)
    must pick up that handle and short-circuit the tool, so a synthetic run —
    e.g. an rds scenario with alert_source 'cloudwatch' — never reaches a real
    boto3 lookup_events call even when ambient AWS creds are present.
    """
    backend = _FakeAWSBackend(
        response={"source": "cloudtrail", "available": True, "total_events": 0, "events": []}
    )
    # Source dict shaped exactly like tests/synthetic/rds_postgres/run_suite.py.
    sources = {"aws": {"region": "us-east-1", "ec2_backend": backend}}

    params = cloudtrail_extract_params(sources)
    assert params["aws_backend"] is backend  # resolved from ec2_backend, not _backend

    result = lookup_cloudtrail_events(resource_name="sg-1", **params)

    mock_call.assert_not_called()
    assert backend.calls and backend.calls[0]["region"] == "us-east-1"
    assert result["available"] is True
