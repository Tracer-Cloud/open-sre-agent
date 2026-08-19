"""Shared helpers for EKS tool availability and credential extraction.

Used by multiple EKS tool modules; extracted here to avoid circular imports
when individual tools move to their own files.
"""

from __future__ import annotations

from typing import Any


def _eks_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("eks", {}).get("connection_verified"))


def _eks_creds(eks: dict) -> dict[str, Any]:
    return {
        "role_arn": eks.get("role_arn", ""),
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
        "credentials": eks.get("credentials"),
    }
