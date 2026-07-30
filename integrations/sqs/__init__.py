"""Shared AWS SQS integration helpers.

Queue state — depth, in-flight count, oldest-message age, visibility timeout,
and dead-letter wiring — is the canonical signal for backlog and stuck-consumer
failures. It lives in queue attributes rather than in logs or metrics, so a
consumer that hangs without ever raising leaves no trace anywhere else.

Like CloudTrail (and unlike RDS, which is pinned to one configured
``db_instance_identifier``), SQS is account-wide: an empty ``queue_name_prefix``
validly means "list every queue". The tool therefore rides on the account-level
``aws`` integration for availability and region instead of defining its own
credential plumbing.

All AWS API calls are read-only and routed through the shared ``aws_sdk_client``
allowlist (``list_queues`` matches ``^list_.*`` and ``get_queue_attributes``
matches ``^get_.*``), so the integration cannot mutate, consume, or delete
messages.
"""

from __future__ import annotations

from typing import Any

from integrations._relational import env_str

DEFAULT_SQS_REGION = "us-east-1"
DEFAULT_SQS_MAX_QUEUES = 20
MAX_SQS_MAX_QUEUES = 100


def coerce_sqs_max_queues(raw_max_queues: Any) -> int:
    """Clamp a caller-supplied queue limit into the supported range.

    Planner-supplied values arrive unvalidated (and may be strings), so this is
    the single normalization path shared by ``sqs_extract_params`` and the tool
    body — keeping the config default, the schema bounds, and the runtime clamp
    from drifting apart.
    """
    try:
        parsed = int(raw_max_queues)
    except (TypeError, ValueError):
        return DEFAULT_SQS_MAX_QUEUES
    return max(1, min(MAX_SQS_MAX_QUEUES, parsed))


def sqs_is_available(sources: dict[str, dict]) -> bool:
    """Check whether SQS queues can be inspected for this investigation.

    Queue inspection only needs AWS account access (credentials + region), which
    the account-level ``aws`` integration already provides. We therefore gate on
    the ``aws`` source the catalog populates from ``AWSIntegrationConfig`` —
    mirroring CloudTrail — plus the optional synthetic ``ec2_backend`` handle
    (the key the synthetic harness injects into the ``aws`` source) so the tool
    stays selectable in fixture-driven tests.

    Gating on a dedicated ``sqs`` source instead would leave the tool
    permanently unavailable for the common setup, where one IAM role serves
    EC2/RDS/Lambda/SQS through a single ``aws`` integration and nothing prompts
    the user to register a second, SQS-specific credential record.

    Note: ``role_arn`` / ``credentials`` here gate *availability* only. The
    actual calls run through ``execute_aws_sdk_call``, which uses boto3's
    ambient credential chain (env / shared config / instance role) — the
    configured role is not assumed as the execution identity. This matches the
    other AWS tools (RDS/EKS/CloudTrail).
    """
    aws = sources.get("aws", {})
    return bool(
        aws.get("connection_verified")
        or aws.get("role_arn")
        or aws.get("credentials")
        or aws.get("ec2_backend")
    )


def sqs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract SQS call params (region) from the ``aws`` source.

    Resolution order for region matches the rest of the AWS stack: explicit
    ``aws`` source field, then ``AWS_REGION`` env, then the default. Forwards
    the optional synthetic ``ec2_backend`` handle as ``aws_backend`` so the tool
    short-circuits to fixture data instead of leaking boto3 calls to whatever
    AWS account the developer happens to be authenticated against during a
    synthetic run.

    ``queue_name_prefix`` is deliberately not extracted here: which queue matters
    is alert-specific, so the planner supplies it at call time. Omitting it means
    "inspect every queue", which is the right default for discovery.
    """
    aws = sources.get("aws", {})
    region = str(aws.get("region") or "").strip() or env_str("AWS_REGION") or DEFAULT_SQS_REGION
    return {
        "region": region,
        "aws_backend": aws.get("ec2_backend"),
    }
