"""Shared boto3 client helpers for gateway EC2 deployment."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

DEFAULT_REGION = "us-east-1"


def get_boto3_client(service: str, region: str = DEFAULT_REGION) -> Any:
    """Get a boto3 client with standard retry configuration."""
    config = Config(
        retries={"max_attempts": 3, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=30,
    )
    return boto3.client(service, region_name=region, config=config)  # type: ignore[call-overload]


def get_standard_tags(stack_name: str) -> list[dict[str, str]]:
    """Return standard resource tags for a gateway deployment stack."""
    return [
        {"Key": "tracer:stack", "Value": stack_name},
        {"Key": "tracer:managed", "Value": "sdk"},
    ]
