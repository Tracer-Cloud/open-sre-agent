"""Shared AWS SQS integration helpers.

Provides configuration normalization, source detection, and parameter
extraction for the SQS investigation tools. All AWS API calls are
read-only and routed through the shared aws_sdk_client allowlist.
"""

from __future__ import annotations

from typing import Any

from app.integrations._relational import env_str
from app.strict_config import StrictConfigModel

DEFAULT_SQS_REGION = "us-east-1"


class SQSConfig(StrictConfigModel):
    """Normalized SQS connection settings."""

    queue_name_prefix: str = ""
    region: str = DEFAULT_SQS_REGION
    integration_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.region)


def build_sqs_config(raw: dict[str, Any] | None) -> SQSConfig:
    """Build a normalized SQS config object from env/store data."""
    return SQSConfig.model_validate(raw or {})


def sqs_config_from_env() -> SQSConfig | None:
    """Load an SQS config from env vars."""
    region = env_str("AWS_REGION") or env_str("SQS_REGION") or DEFAULT_SQS_REGION
    return build_sqs_config(
        {
            "queue_name_prefix": env_str("SQS_QUEUE_NAME_PREFIX") or "",
            "region": region,
        }
    )


def sqs_is_available(sources: dict[str, dict]) -> bool:
    """Check if SQS integration identifying params are present."""
    sqs = sources.get("sqs", {})
    if sqs.get("_backend") or sqs.get("connection_verified"):
        return True
    return (
        any(
            sources.get(k, {}).get("_backend") or sources.get(k, {}).get("connection_verified")
            for k in ("rds", "ec2", "eks", "cloudwatch")
        )
        or "cloudwatch" in sources
    )


def sqs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract SQS identifying params (region, aws_backend)."""
    sqs = sources.get("sqs", {})
    rds = sources.get("rds", {})
    ec2 = sources.get("ec2", {})
    eks = sources.get("eks", {})

    backend = (
        sqs.get("_backend") or rds.get("_backend") or ec2.get("_backend") or eks.get("_backend")
    )

    region = (
        str(sqs.get("region") or "").strip()
        or str(rds.get("region") or "").strip()
        or str(ec2.get("region") or "").strip()
        or str(eks.get("region") or "").strip()
        or env_str("AWS_REGION")
        or env_str("SQS_REGION")
        or DEFAULT_SQS_REGION
    )

    return {
        "region": region,
        "aws_backend": backend,
    }
