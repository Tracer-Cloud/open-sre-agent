"""Local probe for the ambient AWS credential chain the role mode relies on."""

from __future__ import annotations

import boto3

# What each ambient source is, in the words a user needs to act on it.
AMBIENT_SOURCES_HINT = (
    "environment keys (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY), "
    "an `aws configure` profile in ~/.aws/credentials, "
    "or an attached instance/task role"
)


def has_ambient_credentials() -> bool:
    """True when boto3 can resolve base credentials without any prompt.

    A local lookup only — env, shared files, and the lazily-consulted instance
    metadata provider — so it never blocks setup on a network round trip.
    """
    try:
        return boto3.session.Session().get_credentials() is not None
    except Exception:
        return False


__all__ = ["AMBIENT_SOURCES_HINT", "has_ambient_credentials"]
