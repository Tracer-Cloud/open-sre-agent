"""Shared AWS SQS integration helpers.

Provides configuration normalization, source detection, and parameter
extraction for the SQS investigation tools. All AWS API calls are
read-only and routed through the shared aws_sdk_client allowlist.
"""

from __future__ import annotations

import os
from typing import Any

from app.integrations._relational import env_str
from app.strict_config import StrictConfigModel

DEFAULT_SQS_REGION = "us-east-1"
DEFAULT_SQS_MAX_QUEUES = 20


class SQSConfig(StrictConfigModel):
    """Normalized SQS connection settings."""

    queue_name_prefix: str = ""
    region: str = DEFAULT_SQS_REGION
    max_queues: int = DEFAULT_SQS_MAX_QUEUES


def coerce_sqs_max_queues(raw_max_queues: Any) -> int:
    """Normalize the max_queues value into the supported range [1, 100]."""
    try:
        parsed = int(raw_max_queues)
    except (TypeError, ValueError):
        return DEFAULT_SQS_MAX_QUEUES
    return max(1, min(100, parsed))


def build_sqs_config(raw: dict[str, Any] | None) -> SQSConfig:
    """Build a normalized SQS config object from env/store data."""
    return SQSConfig.model_validate(raw or {})


def sqs_config_from_env() -> SQSConfig | None:
    """Load an SQS config from env vars."""
    region = env_str("AWS_REGION") or env_str("SQS_REGION")
    if not region:
        return None
    return build_sqs_config(
        {
            "queue_name_prefix": env_str("SQS_QUEUE_NAME_PREFIX"),
            "region": region,
        }
    )


def sqs_is_available(sources: dict[str, dict]) -> bool:
    """Check if SQS integration is available.

    A scenario-injected ``_backend`` (FixtureAWSBackend in synthetic tests)
    counts on its own. Otherwise, the integration is available when the sqs
    source dict carries config, or when AWS_REGION / SQS_REGION is set in
    the environment (same credential path as EKS/CloudWatch).
    """
    sqs = sources.get("sqs", {})
    if sqs.get("_backend"):
        return True
    if sqs:
        return True
    return bool(os.getenv("AWS_REGION") or os.getenv("SQS_REGION"))


def sqs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract SQS params (queue_name_prefix, max_queues, region).

    Forwards the optional synthetic ``_backend`` handle as ``aws_backend`` so
    the SQS tool can short-circuit to fixture data instead of hitting real
    boto3 during synthetic test runs.
    """
    sqs = sources.get("sqs", {})
    region = (
        str(sqs.get("region") or "").strip()
        or env_str("AWS_REGION")
        or env_str("SQS_REGION")
        or DEFAULT_SQS_REGION
    )
    max_queues = coerce_sqs_max_queues(sqs.get("max_queues", DEFAULT_SQS_MAX_QUEUES))
    return {
        "queue_name_prefix": str(sqs.get("queue_name_prefix") or "").strip(),
        "max_queues": max_queues,
        "region": region,
        "aws_backend": sqs.get("_backend"),
    }
