"""Shared AWS SQS integration helpers.

Like CloudTrail (and unlike RDS, which is pinned to one configured
``db_instance_identifier``), SQS is account-wide — an empty ``queue_name_prefix``
validly means "list every queue" — so it rides on the account-level ``aws``
integration for availability and region rather than defining its own credential
plumbing.

All AWS API calls are read-only and routed through the shared ``aws_sdk_client``
allowlist, so the integration cannot mutate, consume, or delete messages.
"""

from __future__ import annotations

from typing import Any

from integrations._relational import env_str

DEFAULT_SQS_REGION = "us-east-1"
DEFAULT_SQS_MAX_QUEUES = 20
MAX_SQS_MAX_QUEUES = 100


def coerce_sqs_max_queues(raw_max_queues: Any) -> int:
    """Clamp a queue limit into the supported range.

    Planner-supplied values arrive unvalidated and may be strings.
    """
    try:
        parsed = int(raw_max_queues)
    except (TypeError, ValueError):
        return DEFAULT_SQS_MAX_QUEUES
    return max(1, min(MAX_SQS_MAX_QUEUES, parsed))


def sqs_is_available(sources: dict[str, dict]) -> bool:
    """Gate on the account-level ``aws`` source, not a dedicated ``sqs`` one.

    A dedicated source would leave the tool unavailable for the common setup,
    where one ``aws`` integration serves EC2/RDS/Lambda/SQS and nothing prompts
    a second, SQS-specific credential record. ``ec2_backend`` is the key the
    synthetic harness injects for fixture-driven tests.

    ``role_arn`` / ``credentials`` gate availability only — the actual calls run
    through ``execute_aws_sdk_call``'s ambient boto3 credential chain, not the
    configured role (matches RDS/EKS/CloudTrail).
    """
    aws = sources.get("aws", {})
    return bool(
        aws.get("connection_verified")
        or aws.get("role_arn")
        or aws.get("credentials")
        or aws.get("ec2_backend")
    )


def sqs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract region from the ``aws`` source; forward the synthetic backend.

    ``queue_name_prefix`` is not extracted here — it's alert-specific, so the
    planner supplies it at call time. Omitting it means "inspect every queue".
    """
    aws = sources.get("aws", {})
    region = str(aws.get("region") or "").strip() or env_str("AWS_REGION") or DEFAULT_SQS_REGION
    return {
        "region": region,
        "aws_backend": aws.get("ec2_backend"),
    }
