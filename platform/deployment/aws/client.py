"""Shared boto3 client helpers for OpenSRE infrastructure deployments."""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.config import Config

from platform.deployment.aws.config import (
    BOTO3_CONNECT_TIMEOUT_SECONDS,
    BOTO3_READ_TIMEOUT_SECONDS,
    BOTO3_RETRY_MAX_ATTEMPTS,
    DEFAULT_REGION,
    DEPLOY_ACCOUNT_ID_ENV,
    MANAGED_TAG_KEY,
    MANAGED_TAG_VALUE,
    STACK_TAG_KEY,
)


class DeployAccountError(RuntimeError):
    """Raised when the active AWS account is not the configured deploy account."""


def assert_deploy_account(region: str = DEFAULT_REGION) -> str:
    """Abort unless the active AWS account matches ``OPENSRE_DEPLOY_ACCOUNT_ID``.

    Opt-in guard: when the env var is set (local ``.env``), it stops the default
    AWS profile from creating opensre resources in the wrong account. Unset
    (other devs, CI) means no enforcement and no STS call. Returns the active
    account id when enforced, else an empty string.
    """
    expected = os.getenv(DEPLOY_ACCOUNT_ID_ENV, "").strip()
    if not expected:
        return ""
    active = str(get_boto3_client("sts", region).get_caller_identity()["Account"])
    if active != expected:
        raise DeployAccountError(
            f"Active AWS account {active} is not the configured opensre deploy account "
            f"{expected}. Point AWS_PROFILE at the opensre account, or unset "
            f"{DEPLOY_ACCOUNT_ID_ENV}. Refusing to create resources here."
        )
    return active


def get_boto3_client(service: str, region: str = DEFAULT_REGION) -> Any:
    """Get a boto3 client with standard retry configuration."""
    config = Config(
        retries={"max_attempts": BOTO3_RETRY_MAX_ATTEMPTS, "mode": "adaptive"},
        connect_timeout=BOTO3_CONNECT_TIMEOUT_SECONDS,
        read_timeout=BOTO3_READ_TIMEOUT_SECONDS,
    )
    return boto3.client(service, region_name=region, config=config)  # type: ignore[call-overload]


def get_standard_tags(stack_name: str) -> list[dict[str, str]]:
    """Return standard resource tags for an OpenSRE deployment stack."""
    return [
        {"Key": STACK_TAG_KEY, "Value": stack_name},
        {"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE},
    ]
