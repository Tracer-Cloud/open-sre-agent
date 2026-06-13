"""AWS CloudTrail Event Lookup Tool."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.integrations.sqs import sqs_extract_params, sqs_is_available
from app.services.aws_sdk_client import execute_aws_sdk_call
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)

DEFAULT_DURATION_MINUTES = 60
DEFAULT_REGION = "us-east-1"


@tool(
    name="lookup_cloudtrail_events",
    source="cloudtrail",
    description=(
        "Look up AWS CloudTrail events for configuration-change forensics (who changed what, and when)."
    ),
    use_cases=[
        "Investigating unauthorized or unexpected AWS configuration changes",
        "Finding which IAM principal performed a specific action",
        "Correlating system mutations with incident timeframes",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "resource_name": {
                "type": "string",
                "description": "Optional name of the resource (e.g., db-name, role-name).",
            },
            "event_source": {
                "type": "string",
                "description": "Optional event source (e.g., rds.amazonaws.com, iam.amazonaws.com).",
            },
            "username": {
                "type": "string",
                "description": "Optional username or IAM principal who made the change.",
            },
            "duration_minutes": {
                "type": "integer",
                "default": DEFAULT_DURATION_MINUTES,
                "minimum": 1,
                "maximum": 20160,
            },
            "region": {"type": "string", "default": DEFAULT_REGION},
        },
    },
    is_available=sqs_is_available,
    extract_params=sqs_extract_params,
)
def lookup_cloudtrail_events(
    resource_name: str | None = None,
    event_source: str | None = None,
    username: str | None = None,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    region: str = DEFAULT_REGION,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Look up AWS CloudTrail events within a specific duration.

    If aws_backend is provided, delegates to it.
    Otherwise executes boto3 read-only calls via execute_aws_sdk_call.
    """
    logger.info(
        "[cloudtrail] lookup_cloudtrail_events resource=%s source=%s user=%s duration=%s region=%s",
        resource_name,
        event_source,
        username,
        duration_minutes,
        region,
    )

    if aws_backend is not None:
        try:
            return cast(
                "dict[str, Any]",
                aws_backend.lookup_events(
                    resource_name=resource_name,
                    event_source=event_source,
                    username=username,
                    duration_minutes=duration_minutes,
                    region=region,
                ),
            )
        except Exception as exc:
            logger.error("[cloudtrail] aws_backend call failed: %s", exc)
            return {
                "source": "cloudtrail",
                "available": False,
                "error": f"Backend call failed: {exc}",
                "events": [],
            }

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(minutes=duration_minutes)

    parameters: dict[str, Any] = {
        "StartTime": start_time,
        "EndTime": end_time,
    }

    lookup_attributes = []
    if resource_name:
        lookup_attributes.append({"AttributeKey": "ResourceName", "AttributeValue": resource_name})
    if event_source:
        lookup_attributes.append({"AttributeKey": "EventSource", "AttributeValue": event_source})
    if username:
        lookup_attributes.append({"AttributeKey": "Username", "AttributeValue": username})

    if lookup_attributes:
        parameters["LookupAttributes"] = lookup_attributes

    res = execute_aws_sdk_call(
        service_name="cloudtrail",
        operation_name="lookup_events",
        parameters=parameters,
        region=region,
    )

    if not res.get("success"):
        error_msg = res.get("error") or "Unknown error"
        logger.error("[cloudtrail] lookup_events failed: %s", error_msg)
        return {
            "source": "cloudtrail",
            "available": False,
            "error": f"Failed to look up CloudTrail events: {error_msg}",
            "events": [],
        }

    raw_events = (res.get("data") or {}).get("Events") or []
    events_out = []
    for event in raw_events:
        resources = [
            {
                "type": r.get("ResourceType"),
                "name": r.get("ResourceName"),
            }
            for r in event.get("Resources", [])
        ]
        events_out.append(
            {
                "event_id": event.get("EventId"),
                "event_name": event.get("EventName"),
                "event_time": event.get("EventTime"),
                "event_source": event.get("EventSource"),
                "username": event.get("Username"),
                "resources": resources,
            }
        )

    return {
        "source": "cloudtrail",
        "available": True,
        "region": region,
        "total_events": len(events_out),
        "events": events_out,
        "error": None,
    }
