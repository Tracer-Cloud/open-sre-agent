"""Shared AWS CloudTrail integration helpers.

CloudTrail is the canonical AWS change-causality source — "who changed what,
and when?". Unlike RDS, CloudTrail is account-wide rather than tied to a single
configured resource, so the CloudTrail tool rides on the account-level ``aws``
integration (``AWSIntegrationConfig``) for availability and region rather than
defining its own credential plumbing.

All AWS API calls are read-only and routed through the shared
``aws_sdk_client`` allowlist (``lookup_events`` matches ``^lookup_.*``), so the
integration cannot mutate any resources.
"""

from __future__ import annotations

from typing import Any

from integrations._relational import env_str

DEFAULT_CLOUDTRAIL_REGION = "us-east-1"


def cloudtrail_is_available(sources: dict[str, dict]) -> bool:
    """Check whether CloudTrail can be queried for this investigation.

    CloudTrail forensics only needs AWS account access (credentials + region),
    which the account-level ``aws`` integration already provides. We therefore
    gate availability on the ``aws`` source the catalog populates from
    ``AWSIntegrationConfig`` — mirroring how the other AWS tools reuse the creds
    wired via the EKS/CloudWatch path — and on the optional synthetic
    ``ec2_backend`` handle (the key the synthetic harness injects into the
    ``aws`` source) so the tool stays selectable in fixture-driven tests.

    Note: ``role_arn`` / ``credentials`` here gate *availability* only. The
    actual lookup runs through ``execute_aws_sdk_call``, which uses boto3's
    ambient credential chain (env / shared config / instance role) — the
    configured role is not assumed as the execution identity. This matches the
    other AWS tools (RDS/EKS).
    """
    aws = sources.get("aws", {})
    return bool(
        aws.get("connection_verified")
        or aws.get("role_arn")
        or aws.get("credentials")
        or aws.get("ec2_backend")
    )


def cloudtrail_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract CloudTrail call params (region) from the ``aws`` source.

    Resolution order for region matches the rest of the AWS stack: explicit
    ``aws`` source field, then ``AWS_REGION`` env, then the default. Forwards
    the optional synthetic ``ec2_backend`` handle (the key the synthetic harness
    injects into the ``aws`` source) as ``aws_backend`` so the tool short-circuits
    to fixture data instead of leaking boto3 calls to whatever AWS account the
    developer happens to be authenticated against during a synthetic run.
    Resource/principal/time-window filters are alert-specific and are supplied
    by the planner at call time, not extracted here.
    """
    aws = sources.get("aws", {})
    region = (
        str(aws.get("region") or "").strip() or env_str("AWS_REGION") or DEFAULT_CLOUDTRAIL_REGION
    )
    return {
        "region": region,
        "aws_backend": aws.get("ec2_backend"),
    }
