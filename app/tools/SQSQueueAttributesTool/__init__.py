"""SQS queue attributes tool — depth, in-flight count, visibility timeout, and DLQ wiring."""

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


def _queue_name_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _parse_attributes(raw_attrs: dict[str, str]) -> dict[str, Any]:
    """Normalize raw SQS attribute strings into typed fields."""

    def _int(key: str) -> int | None:
        val = raw_attrs.get(key)
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    visible = _int("ApproximateNumberOfMessages")
    in_flight = _int("ApproximateNumberOfMessagesNotVisible")
    oldest_age = _int("ApproximateAgeOfOldestMessage")
    visibility_timeout = _int("VisibilityTimeout")

    redrive_raw = raw_attrs.get("RedrivePolicy")
    redrive_policy: dict[str, Any] | None = None
    if redrive_raw:
        try:
            redrive_policy = json.loads(redrive_raw)
        except (json.JSONDecodeError, TypeError):
            redrive_policy = {"raw": redrive_raw}

    fifo_attr = raw_attrs.get("FifoQueue", "").lower()
    content_dedup = raw_attrs.get("ContentBasedDeduplication", "").lower()

    return {
        "visible_count": visible,
        "in_flight_count": in_flight,
        "oldest_message_age_seconds": oldest_age,
        "visibility_timeout_seconds": visibility_timeout,
        "has_dlq": redrive_policy is not None,
        "redrive_policy": redrive_policy,
        "is_fifo": fifo_attr == "true",
        "content_based_deduplication": content_dedup == "true",
    }


@tool(
    name="get_sqs_queue_attributes",
    source="sqs",
    description=(
        "List AWS SQS queues by name prefix and return per-queue attributes — "
        "visible message depth, in-flight count, oldest-message age, visibility "
        "timeout, DLQ wiring, and FIFO flag. Use this to distinguish a normal "
        "backlog (visible high, in-flight low) from stuck consumers (visible ≈ 0, "
        "in-flight = pod count, old messages, no DLQ)."
    ),
    use_cases=[
        "Diagnosing stuck consumers: in-flight count equals pod count with no DLQ",
        "Identifying poison-pill messages cycling due to short VisibilityTimeout",
        "Checking whether a queue has a dead-letter queue configured",
        "Assessing queue backlog depth when an ApproximateAgeOfOldestMessage alert fires",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "queue_name_prefix": {
                "type": "string",
                "default": "",
                "description": "Filter queues whose name starts with this prefix. Empty string lists all queues.",
            },
            "max_queues": {
                "type": "integer",
                "default": DEFAULT_MAX_QUEUES,
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of queues to inspect (default 20, max 100).",
            },
            "region": {"type": "string", "default": DEFAULT_SQS_REGION},
        },
    },
    is_available=sqs_is_available,
    extract_params=sqs_extract_params,
    surfaces=("investigation", "chat"),
)
def get_sqs_queue_attributes(
    queue_name_prefix: str = "",
    max_queues: int = DEFAULT_MAX_QUEUES,
    region: str = DEFAULT_SQS_REGION,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return per-queue SQS attributes for queues matching the given prefix.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests)
    the call short-circuits to the backend so we never leak boto3 calls to
    real AWS during scenario runs.
    """
    logger.info(
        "[sqs] get_sqs_queue_attributes prefix=%r max_queues=%s region=%s",
        queue_name_prefix,
        max_queues,
        region,
    )

    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.get_sqs_queue_attributes(
                queue_name_prefix=queue_name_prefix,
                max_queues=max_queues,
                region=region,
            ),
        )

    capped = max(1, min(100, max_queues))
    list_params: dict[str, Any] = {"MaxResults": capped}
    if queue_name_prefix:
        list_params["QueueNamePrefix"] = queue_name_prefix

    list_result = execute_aws_sdk_call(
        service_name="sqs",
        operation_name="list_queues",
        parameters=list_params,
        region=region,
    )

    if not list_result.get("success"):
        logger.error(
            "[sqs] list_queues failed prefix=%r region=%s: %s",
            queue_name_prefix,
            region,
            list_result.get("error"),
        )
        return {
            "source": "sqs",
            "available": False,
            "queue_name_prefix": queue_name_prefix,
            "error": "Failed to list SQS queues. Check server logs for details.",
        }

    queue_urls: list[str] = (list_result.get("data") or {}).get("QueueUrls") or []
    queues: list[dict[str, Any]] = []

    for url in queue_urls:
        name = _queue_name_from_url(url)
        attr_result = execute_aws_sdk_call(
            service_name="sqs",
            operation_name="get_queue_attributes",
            parameters={"QueueUrl": url, "AttributeNames": ["All"]},
            region=region,
        )
        if not attr_result.get("success"):
            logger.warning(
                "[sqs] get_queue_attributes failed queue=%s region=%s: %s",
                name,
                region,
                attr_result.get("error"),
            )
            queues.append(
                {
                    "name": name,
                    "url": url,
                    "attributes_error": "Failed to retrieve attributes. Check server logs.",
                }
            )
            continue

        raw_attrs: dict[str, str] = (attr_result.get("data") or {}).get("Attributes") or {}
        parsed = _parse_attributes(raw_attrs)
        queues.append({"name": name, "url": url, **parsed})

    return {
        "source": "sqs",
        "available": True,
        "queue_name_prefix": queue_name_prefix,
        "region": region,
        "total_queues": len(queues),
        "queues": queues,
        "error": None,
    }
