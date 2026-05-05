"""RDS events tool — recent failover, maintenance, and configuration events."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations.rds import (
    DEFAULT_RDS_REGION,
    rds_extract_params,
    rds_is_available,
)
from app.services.aws_sdk_client import execute_aws_sdk_call
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)

DEFAULT_DURATION_MINUTES = 60


@tool(
    name="describe_rds_events",
    source="rds",
    description=(
        "Describe recent AWS RDS events for a DB instance — failovers, "
        "maintenance windows, parameter changes, and backup events."
    ),
    use_cases=[
        "Investigating Multi-AZ failover events",
        "Checking recent maintenance or parameter group changes",
        "Tracing backup or recovery events around an incident",
    ],
    requires=["db_instance_identifier"],
    input_schema={
        "type": "object",
        "properties": {
            "db_instance_identifier": {"type": "string"},
            "region": {"type": "string", "default": DEFAULT_RDS_REGION},
            "duration_minutes": {
                "type": "integer",
                "default": DEFAULT_DURATION_MINUTES,
                "minimum": 1,
                "maximum": 20160,
            },
        },
        "required": ["db_instance_identifier"],
    },
    is_available=rds_is_available,
    extract_params=rds_extract_params,
)
def describe_rds_events(
    db_instance_identifier: str,
    region: str = DEFAULT_RDS_REGION,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe recent RDS events for a DB instance."""
    logger.info(
        "[rds] describe_rds_events db=%s region=%s duration=%s",
        db_instance_identifier,
        region,
        duration_minutes,
    )

    result = execute_aws_sdk_call(
        service_name="rds",
        operation_name="describe_events",
        parameters={
            "SourceIdentifier": db_instance_identifier,
            "SourceType": "db-instance",
            "Duration": duration_minutes,
        },
        region=region,
    )

    if not result.get("success"):
        logger.error(
            "[rds] describe_events failed for db=%s region=%s: %s",
            db_instance_identifier,
            region,
            result.get("error"),
        )
        return {
            "source": "rds",
            "available": False,
            "db_instance_identifier": db_instance_identifier,
            "error": "Failed to describe RDS events. Check server logs for details.",
        }

    raw_events = (result.get("data") or {}).get("Events") or []
    events = [
        {
            "date": event.get("Date"),
            "message": event.get("Message"),
            "categories": event.get("EventCategories", []),
            "source_type": event.get("SourceType"),
        }
        for event in raw_events
    ]

    return {
        "source": "rds",
        "available": True,
        "db_instance_identifier": db_instance_identifier,
        "duration_minutes": duration_minutes,
        "total_events": len(events),
        "events": events,
        "error": None,
    }
