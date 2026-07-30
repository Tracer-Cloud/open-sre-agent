"""Env names for optional remote context sync to a user-owned object store.

Opt-in and off by default. A laptop keeps working entirely on local disk; when
enabled, conversation history and memory are mirrored to a store the user owns
so a second machine can pick up where the first left off.

The backend is selected by ``OPENSRE_REMOTE_SYNC_PROVIDER`` (default ``aws``).
New vendors register under ``platform.filestorage.providers`` without changing
the sync engine.
"""

from __future__ import annotations

# Master switch. Sync stays off until this is truthy, even if a bucket is named.
REMOTE_SYNC_ENV = "OPENSRE_REMOTE_SYNC"
# Cloud provider registered in platform.filestorage.providers (default: aws).
# Value must stay aligned with BuiltInProvider.AWS in platform.filestorage.enums.
REMOTE_SYNC_PROVIDER_ENV = "OPENSRE_REMOTE_SYNC_PROVIDER"
# Top-level store name the user owns (S3 bucket today; other providers may reuse).
# Required when sync is on.
REMOTE_SYNC_BUCKET_ENV = "OPENSRE_REMOTE_SYNC_BUCKET"
# Key prefix inside the store, so one store can hold several roots.
REMOTE_SYNC_PREFIX_ENV = "OPENSRE_REMOTE_SYNC_PREFIX"
# Region override; falls back to the ambient cloud configuration when supported.
REMOTE_SYNC_REGION_ENV = "OPENSRE_REMOTE_SYNC_REGION"
# Named credentials profile, for users who keep opensre credentials separate.
REMOTE_SYNC_PROFILE_ENV = "OPENSRE_REMOTE_SYNC_PROFILE"

DEFAULT_REMOTE_SYNC_PREFIX = "opensre"
DEFAULT_REMOTE_SYNC_PROVIDER = "aws"

__all__ = [
    "DEFAULT_REMOTE_SYNC_PREFIX",
    "DEFAULT_REMOTE_SYNC_PROVIDER",
    "REMOTE_SYNC_BUCKET_ENV",
    "REMOTE_SYNC_ENV",
    "REMOTE_SYNC_PREFIX_ENV",
    "REMOTE_SYNC_PROFILE_ENV",
    "REMOTE_SYNC_PROVIDER_ENV",
    "REMOTE_SYNC_REGION_ENV",
]
