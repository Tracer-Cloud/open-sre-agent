"""CloudTrail events tool — "who changed what, and when?" change forensics.

Wraps the read-only CloudTrail ``lookup_events`` API so the planner can trace
configuration-change causality during an AWS incident: IAM changes,
security-group mutations, EKS/Lambda config updates, and resource deletions.

CloudTrail's ``LookupAttributes`` accepts only ONE filter attribute per call,
so the tool exposes the common forensic filters (resource name, event source,
principal/username) as optional params and sends the most specific one that was
provided. A ``duration_minutes`` window is converted to the ``StartTime`` /
``EndTime`` pair the API expects.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.cloudtrail import (
    DEFAULT_CLOUDTRAIL_REGION,
    cloudtrail_extract_params,
    cloudtrail_is_available,
)
from tools.tool_decorator import tool

logger = logging.getLogger(__name__)

DEFAULT_DURATION_MINUTES = 60
# CloudTrail caps lookups at 90 days of history and 50 events per page.
MAX_DURATION_MINUTES = 90 * 24 * 60
DEFAULT_MAX_RESULTS = 50
MAX_RESULTS_LIMIT = 50


def _build_lookup_attribute(
    resource_name: str,
    event_source: str,
    username: str,
) -> list[dict[str, str]]:
    """Build the single-item ``LookupAttributes`` list CloudTrail allows.

    CloudTrail rejects more than one attribute per request, so we pick the most
    specific filter that was provided: a concrete resource pins the blast
    radius best, then the acting principal, then the broad service source.
    Returns an empty list when no filter is set (recent account-wide events).
    """
    if resource_name:
        return [{"AttributeKey": "ResourceName", "AttributeValue": resource_name}]
    if username:
        return [{"AttributeKey": "Username", "AttributeValue": username}]
    if event_source:
        return [{"AttributeKey": "EventSource", "AttributeValue": event_source}]
    return []


def _shape_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw CloudTrail event down to the fields RCA actually needs."""
    resources = [
        {
            "type": resource.get("ResourceType"),
            "name": resource.get("ResourceName"),
        }
        for resource in (raw.get("Resources") or [])
    ]

    # CloudTrailEvent is a JSON string carrying the full record; pull the
    # high-signal forensic fields out of it without dumping the whole blob.
    aws_region = source_ip = error_code = None
    detail = raw.get("CloudTrailEvent")
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
        except (ValueError, TypeError):
            parsed = {}
        aws_region = parsed.get("awsRegion")
        source_ip = parsed.get("sourceIPAddress")
        error_code = parsed.get("errorCode")

    # CloudTrail returns ReadOnly as the string "true"/"false"; coerce to a real
    # bool so callers don't trip over "false" being truthy in Python.
    read_only_raw = raw.get("ReadOnly")
    if isinstance(read_only_raw, bool):
        read_only = read_only_raw
    elif isinstance(read_only_raw, str):
        read_only = read_only_raw.strip().lower() == "true"
    else:
        read_only = None

    return {
        "event_id": raw.get("EventId"),
        "event_name": raw.get("EventName"),
        "event_time": raw.get("EventTime"),
        "event_source": raw.get("EventSource"),
        "username": raw.get("Username"),
        "read_only": read_only,
        "access_key_id": raw.get("AccessKeyId"),
        "resources": resources,
        "aws_region": aws_region,
        "source_ip_address": source_ip,
        "error_code": error_code,
    }


@tool(
    name="lookup_cloudtrail_events",
    display_name="CloudTrail",
    source="cloudtrail",
    description=(
        "Look up recent AWS CloudTrail management events to answer 'who changed "
        "what, and when?' — IAM changes, security-group mutations, EKS/Lambda "
        "config updates, and resource deletions. Filter by resource name, event "
        "source (e.g. iam.amazonaws.com), or username over a time window."
    ),
    use_cases=[
        "Finding who modified an IAM policy, role, or security group before an incident",
        "Tracing config changes to a specific resource (by ResourceName)",
        "Auditing all actions taken by a principal/user (by Username)",
        "Reviewing recent activity from one AWS service (by EventSource)",
        "Establishing change causality at the start of a post-mortem",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Filter by a specific resource name/ARN (most specific filter).",
            },
            "event_source": {
                "type": "string",
                "description": "Filter by AWS service event source, e.g. 'iam.amazonaws.com'.",
            },
            "username": {
                "type": "string",
                "description": "Filter by the acting principal / IAM username.",
            },
            "region": {"type": "string", "default": DEFAULT_CLOUDTRAIL_REGION},
            "duration_minutes": {
                "type": "integer",
                "default": DEFAULT_DURATION_MINUTES,
                "minimum": 1,
                "maximum": MAX_DURATION_MINUTES,
                "description": "Look-back window in minutes (CloudTrail keeps 90 days).",
            },
            "max_results": {
                "type": "integer",
                "default": DEFAULT_MAX_RESULTS,
                "minimum": 1,
                "maximum": MAX_RESULTS_LIMIT,
            },
            "next_token": {
                "type": "string",
                "default": "",
                "description": (
                    "Pagination token from a previous truncated response; pass it to "
                    "fetch the next page of events."
                ),
            },
        },
        "required": [],
    },
    injected_params=("aws_backend",),
    is_available=cloudtrail_is_available,
    extract_params=cloudtrail_extract_params,
)
def lookup_cloudtrail_events(
    resource_name: str = "",
    event_source: str = "",
    username: str = "",
    region: str = DEFAULT_CLOUDTRAIL_REGION,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    max_results: int = DEFAULT_MAX_RESULTS,
    next_token: str = "",
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Look up recent CloudTrail management events scoped to a filter + window.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests) the
    call short-circuits to the backend so we never leak boto3 calls to real AWS
    during scenario runs. Otherwise calls boto3 cloudtrail via
    ``execute_aws_sdk_call`` using the default boto3 credential chain.
    """
    duration_minutes = max(1, min(duration_minutes, MAX_DURATION_MINUTES))
    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))
    lookup_attributes = _build_lookup_attribute(resource_name, event_source, username)

    logger.info(
        "[cloudtrail] lookup_events region=%s filter=%s duration=%s max=%s",
        region,
        lookup_attributes or "none",
        duration_minutes,
        max_results,
    )

    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.lookup_events(
                lookup_attributes=lookup_attributes,
                duration_minutes=duration_minutes,
                max_results=max_results,
                region=region,
                next_token=next_token,
            ),
        )

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(minutes=duration_minutes)

    parameters: dict[str, Any] = {
        "StartTime": start_time,
        "EndTime": end_time,
        "MaxResults": max_results,
    }
    if lookup_attributes:
        parameters["LookupAttributes"] = lookup_attributes
    if next_token:
        parameters["NextToken"] = next_token

    result = execute_aws_sdk_call(
        service_name="cloudtrail",
        operation_name="lookup_events",
        parameters=parameters,
        region=region,
    )

    if not result.get("success"):
        logger.error(
            "[cloudtrail] lookup_events failed region=%s: %s",
            region,
            result.get("error"),
        )
        return {
            "source": "cloudtrail",
            "available": False,
            "error": "Failed to look up CloudTrail events. Check server logs for details.",
        }

    data = result.get("data") or {}
    raw_events = data.get("Events") or []
    events = [_shape_event(event) for event in raw_events]
    # CloudTrail returns a NextToken when more matching events exist beyond this
    # page. Surface it so the agent knows the result is partial (and can paginate
    # via the next_token param) instead of silently treating 50 events as "all".
    returned_token = data.get("NextToken") or None

    return {
        "source": "cloudtrail",
        "available": True,
        "region": region,
        "duration_minutes": duration_minutes,
        "filter": (lookup_attributes[0] if lookup_attributes else None),
        "total_events": len(events),
        "truncated": bool(returned_token),
        "next_token": returned_token,
        "events": events,
        "error": None,
    }
