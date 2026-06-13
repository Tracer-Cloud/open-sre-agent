"""AWS SQS queue attributes tool."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.integrations.sqs import (
    DEFAULT_SQS_REGION,
    sqs_extract_params,
    sqs_is_available,
)
from app.services.aws_sdk_client import execute_aws_sdk_call
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUEUES = 20


@tool(
    name="get_sqs_queue_attributes",
    source="sqs",
    description=(
        "Fetch operational SQS queue attributes (visible depth, in-flight, oldest-message age, "
        "visibility timeout, and DLQ config) for incident backlog or stuck-consumer diagnosis."
    ),
    use_cases=[
        "Diagnosing message backlogs or stuck consumer processes in SQS-backed queues",
        "Checking visibility timeout and redrive/DLQ settings for poison-pill issues",
        "Listing active SQS queues matching a name prefix",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "queue_name_prefix": {
                "type": "string",
                "description": "Optional string to filter queues by name prefix.",
            },
            "region": {"type": "string", "default": DEFAULT_SQS_REGION},
            "max_queues": {
                "type": "integer",
                "default": DEFAULT_MAX_QUEUES,
                "minimum": 1,
                "maximum": 100,
            },
        },
    },
    is_available=sqs_is_available,
    extract_params=sqs_extract_params,
)
def get_sqs_queue_attributes(
    queue_name_prefix: str = "",
    region: str = DEFAULT_SQS_REGION,
    max_queues: int = DEFAULT_MAX_QUEUES,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List SQS queues by prefix and return their operational attributes.

    If aws_backend is provided, delegates calls to it (for synthetic tests).
    Otherwise executes boto3 read-only calls via execute_aws_sdk_call.
    """
    logger.info(
        "[sqs] get_sqs_queue_attributes prefix=%s region=%s max_queues=%s",
        queue_name_prefix,
        region,
        max_queues,
    )

    if aws_backend is not None:
        try:
            return cast(
                "dict[str, Any]",
                aws_backend.get_sqs_queue_attributes(
                    queue_name_prefix=queue_name_prefix,
                    max_queues=max_queues,
                    region=region,
                ),
            )
        except Exception as exc:
            logger.error("[sqs] aws_backend call failed: %s", exc)
            return {
                "source": "sqs",
                "available": False,
                "error": f"Backend call failed: {exc}",
                "queues": [],
            }

    list_params: dict[str, Any] = {}
    if queue_name_prefix:
        list_params["QueueNamePrefix"] = queue_name_prefix

    list_res = execute_aws_sdk_call(
        service_name="sqs",
        operation_name="list_queues",
        parameters=list_params,
        region=region,
    )

    if not list_res.get("success"):
        error_msg = list_res.get("error") or "Unknown error"
        logger.error("[sqs] list_queues failed: %s", error_msg)
        return {
            "source": "sqs",
            "available": False,
            "error": f"Failed to list SQS queues: {error_msg}",
            "queues": [],
        }

    queue_urls = (list_res.get("data") or {}).get("QueueUrls") or []
    queue_urls = queue_urls[:max_queues]

    queues_out = []
    for url in queue_urls:
        attr_res = execute_aws_sdk_call(
            service_name="sqs",
            operation_name="get_queue_attributes",
            parameters={
                "QueueUrl": url,
                "AttributeNames": ["All"],
            },
            region=region,
        )

        if not attr_res.get("success"):
            error_msg = attr_res.get("error") or "Unknown error"
            logger.error("[sqs] get_queue_attributes failed for %s: %s", url, error_msg)
            return {
                "source": "sqs",
                "available": False,
                "error": f"Failed to get SQS queue attributes: {error_msg}",
                "queues": [],
            }

        raw_attrs = (attr_res.get("data") or {}).get("Attributes") or {}

        redrive = raw_attrs.get("RedrivePolicy")
        redrive_policy = None
        if redrive:
            try:
                redrive_policy = json.loads(redrive)
            except Exception:
                redrive_policy = redrive

        queues_out.append(
            {
                "queue_name": url.split("/")[-1],
                "queue_url": url,
                "queue_arn": raw_attrs.get("QueueArn"),
                "visible_messages": int(raw_attrs.get("ApproximateNumberOfMessages", 0)),
                "in_flight_messages": int(
                    raw_attrs.get("ApproximateNumberOfMessagesNotVisible", 0)
                ),
                "oldest_message_age_seconds": int(
                    raw_attrs.get("ApproximateAgeOfOldestMessage", 0)
                ),
                "visibility_timeout_seconds": int(raw_attrs.get("VisibilityTimeout", 0)),
                "redrive_policy": redrive_policy,
                "has_dlq": bool(redrive_policy),
                "is_fifo": bool(raw_attrs.get("FifoQueue") == "true" or url.endswith(".fifo")),
            }
        )

    return {
        "source": "sqs",
        "available": True,
        "region": region,
        "total_queues": len(queues_out),
        "queues": queues_out,
        "error": None,
    }
