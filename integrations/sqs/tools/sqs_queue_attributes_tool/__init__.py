"""SQS queue attributes tool — backlog depth, stuck consumers, and DLQ wiring.

Wraps the read-only ``list_queues`` + ``get_queue_attributes`` APIs so the
planner can answer "what state is this queue in?" during an incident.

The distinction this tool exists to draw is between a *normal backlog*
(``visible_count`` high, ``in_flight_count`` low — producers outpacing
consumers) and *stuck consumers* (``visible_count`` near zero,
``in_flight_count`` pinned at the consumer-pod count, ``oldest_message_age``
climbing, no DLQ). The second shape is invisible to logs and metrics: a
consumer that hangs without raising never writes an error line, and SQS keeps
redelivering the same message every ``visibility_timeout_seconds`` until every
pod is holding it. Without a ``RedrivePolicy`` there is no receive-count
ceiling to break the cycle.

``list_queues`` returns URLs only, so attributes are fetched per queue; the
``max_queues`` cap bounds that fan-out.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.sqs import (
    DEFAULT_SQS_MAX_QUEUES,
    DEFAULT_SQS_REGION,
    MAX_SQS_MAX_QUEUES,
    coerce_sqs_max_queues,
    sqs_extract_params,
    sqs_is_available,
)

logger = logging.getLogger(__name__)


def _queue_name_from_url(url: str) -> str:
    """Derive the queue name from its URL (the final path segment)."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _parse_attributes(raw_attrs: dict[str, str]) -> dict[str, Any]:
    """Normalize the raw SQS attribute string map into typed fields.

    SQS returns every attribute as a string, including numerics and booleans.
    Values are coerced to ``int`` / ``bool`` so the planner can compare them
    without knowing the API's stringly-typed convention. A missing or
    unparseable numeric becomes ``None`` (unknown) rather than ``0``, which
    would read as a real, meaningful "empty queue" measurement.
    """

    def _int(key: str) -> int | None:
        val = raw_attrs.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    # RedrivePolicy is a JSON-encoded string naming the DLQ target and
    # maxReceiveCount. Preserve the raw text if it does not parse so a malformed
    # policy still surfaces as "a DLQ is configured" rather than silently
    # reading as no DLQ at all.
    redrive_raw = raw_attrs.get("RedrivePolicy")
    redrive_policy: dict[str, Any] | None = None
    if redrive_raw:
        try:
            parsed = json.loads(redrive_raw)
        except (ValueError, TypeError):
            parsed = None
        redrive_policy = parsed if isinstance(parsed, dict) else {"raw": redrive_raw}

    return {
        "visible_count": _int("ApproximateNumberOfMessages"),
        "in_flight_count": _int("ApproximateNumberOfMessagesNotVisible"),
        "oldest_message_age_seconds": _int("ApproximateAgeOfOldestMessage"),
        "visibility_timeout_seconds": _int("VisibilityTimeout"),
        "has_dlq": redrive_policy is not None,
        "redrive_policy": redrive_policy,
        "is_fifo": str(raw_attrs.get("FifoQueue", "")).strip().lower() == "true",
    }


@tool(
    name="get_sqs_queue_attributes",
    display_name="SQS queues",
    source="sqs",
    description=(
        "List AWS SQS queues by name prefix and return per-queue attributes — "
        "visible message depth, in-flight count, oldest-message age, visibility "
        "timeout, dead-letter queue wiring, and FIFO flag. Use this to tell a "
        "normal backlog (visible high, in-flight low) apart from stuck consumers "
        "(visible near zero, in-flight pinned at the consumer count, old "
        "messages, no DLQ)."
    ),
    use_cases=[
        "Diagnosing stuck consumers: in-flight count equals the consumer/pod count",
        "Identifying a poison-pill message cycling due to a short VisibilityTimeout",
        "Checking whether a queue has a dead-letter queue configured at all",
        "Assessing backlog depth when an ApproximateAgeOfOldestMessage alert fires",
        "Confirming whether a queue is draining after a consumer deploy or scale-up",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "queue_name_prefix": {
                "type": "string",
                "default": "",
                "description": (
                    "Only inspect queues whose name starts with this prefix "
                    "(e.g. 'payments-'). Omit to inspect every queue."
                ),
            },
            "max_queues": {
                "type": "integer",
                "default": DEFAULT_SQS_MAX_QUEUES,
                "minimum": 1,
                "maximum": MAX_SQS_MAX_QUEUES,
                "description": "Maximum number of queues to inspect.",
            },
            "region": {"type": "string", "default": DEFAULT_SQS_REGION},
        },
        "required": [],
    },
    injected_params=("aws_backend",),
    is_available=sqs_is_available,
    extract_params=sqs_extract_params,
    surfaces=("investigation", "chat"),
)
def get_sqs_queue_attributes(
    queue_name_prefix: str = "",
    max_queues: int = DEFAULT_SQS_MAX_QUEUES,
    region: str = DEFAULT_SQS_REGION,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return per-queue SQS attributes for queues matching the given prefix.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests) the
    call short-circuits to the backend so we never leak boto3 calls to real AWS
    during scenario runs. Otherwise calls boto3 sqs via ``execute_aws_sdk_call``
    using the default boto3 credential chain.
    """
    max_queues = coerce_sqs_max_queues(max_queues)

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

    list_params: dict[str, Any] = {"MaxResults": max_queues}
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
        return tool_unavailable(
            "sqs",
            "Failed to list SQS queues. Check server logs for details.",
            queues=[],
        )

    # The transport sanitizer can replace the tail of an oversized list with a
    # "... (N more items truncated)" marker string, so keep only real str URLs.
    raw_urls = (list_result.get("data") or {}).get("QueueUrls") or []
    queue_urls = [url for url in raw_urls if isinstance(url, str) and url.startswith("http")]
    # list_queues honors MaxResults, but a paginating caller could still hand us
    # more; clamp so the per-queue fan-out stays bounded either way.
    truncated = len(queue_urls) > max_queues
    queue_urls = queue_urls[:max_queues]

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
            # One unreadable queue (e.g. a per-queue IAM policy denying
            # GetQueueAttributes) must not sink the whole investigation — record
            # the gap on that entry and keep inspecting the rest.
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
                    "attributes_error": (
                        "Failed to retrieve attributes. Check server logs for details."
                    ),
                }
            )
            continue

        raw_attrs = (attr_result.get("data") or {}).get("Attributes") or {}
        queues.append({"name": name, "url": url, **_parse_attributes(raw_attrs)})

    # NOTE: the success payload deliberately carries no "error" key. The runtime
    # tool loop flags failure on a truthy "error" (core.execution._normalize_result),
    # so "error": None is tolerated today — but omitting the key entirely keeps
    # this tool independent of that subtlety.
    return {
        "source": "sqs",
        "available": True,
        "region": region,
        "queue_name_prefix": queue_name_prefix,
        "total_queues": len(queues),
        "truncated": truncated,
        "queues": queues,
    }
