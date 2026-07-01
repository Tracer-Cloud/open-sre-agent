"""Backend-aware availability check for CloudWatch tools.

CloudWatch uses IAM-based auth, so availability is gated on the source
key existing rather than a connection-verified flag.
"""

from __future__ import annotations


def cloudwatch_is_available(sources: dict[str, dict]) -> bool:
    """Available when a CloudWatch source is present in the alert context.

    CloudWatch uses IAM-based auth, so availability is gated on the source key
    existing. Tool params like ``job_queue`` are alert-specific and provided by
    the LLM.
    """
    return bool(sources.get("cloudwatch"))
