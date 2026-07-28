"""Env-var names and static identifiers for local secret storage."""

from __future__ import annotations

from typing import Final

# Keyring service id for every OpenSRE secret. Historically named for LLM API
# keys; integration secrets share it so existing entries keep resolving.
KEYRING_SERVICE: Final = "opensre.llm"

# Skip the OS keyring entirely. Fail-closed: nothing is persisted locally, so
# credentials must come from the process environment.
OPENSRE_DISABLE_KEYRING_ENV: Final = "OPENSRE_DISABLE_KEYRING"

# Refuse the on-disk fallback tier even when the OS keyring is unavailable.
# For operators who would rather onboarding fail than have a secret reach disk.
OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV: Final = "OPENSRE_DISABLE_CREDENTIAL_FALLBACK"

# Redirect the fallback store (tests, or a non-default home).
OPENSRE_CREDENTIAL_FALLBACK_PATH_ENV: Final = "OPENSRE_CREDENTIAL_FALLBACK_PATH"

CREDENTIAL_FALLBACK_FILENAME: Final = "credentials.json"

__all__ = [
    "CREDENTIAL_FALLBACK_FILENAME",
    "KEYRING_SERVICE",
    "OPENSRE_CREDENTIAL_FALLBACK_PATH_ENV",
    "OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV",
    "OPENSRE_DISABLE_KEYRING_ENV",
]
