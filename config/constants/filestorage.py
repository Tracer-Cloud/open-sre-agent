"""Env names for optional remote context sync to a user-owned S3 bucket.

Opt-in and off by default. A laptop keeps working entirely on local disk; when
enabled, conversation history and memory are mirrored to a bucket the user owns
so a second machine can pick up where the first left off.
"""

from __future__ import annotations

# Master switch. Sync stays off until this is truthy, even if a bucket is named.
REMOTE_SYNC_ENV = "OPENSRE_REMOTE_SYNC"
# Bucket the user owns. Required when sync is on.
REMOTE_SYNC_BUCKET_ENV = "OPENSRE_REMOTE_SYNC_BUCKET"
# Key prefix inside the bucket, so one bucket can hold several roots.
REMOTE_SYNC_PREFIX_ENV = "OPENSRE_REMOTE_SYNC_PREFIX"
# Region override; falls back to the ambient AWS configuration.
REMOTE_SYNC_REGION_ENV = "OPENSRE_REMOTE_SYNC_REGION"
# Named AWS profile, for users who keep opensre credentials separate.
REMOTE_SYNC_PROFILE_ENV = "OPENSRE_REMOTE_SYNC_PROFILE"

DEFAULT_REMOTE_SYNC_PREFIX = "opensre"

__all__ = [
    "DEFAULT_REMOTE_SYNC_PREFIX",
    "REMOTE_SYNC_BUCKET_ENV",
    "REMOTE_SYNC_ENV",
    "REMOTE_SYNC_PREFIX_ENV",
    "REMOTE_SYNC_PROFILE_ENV",
    "REMOTE_SYNC_REGION_ENV",
]
