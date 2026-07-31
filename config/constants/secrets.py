"""Env-var names and static identifiers for local secret storage."""

from __future__ import annotations

from typing import Final

# Keyring service id for every OpenSRE secret. Historically named for LLM API
# keys; integration secrets share it so existing entries keep resolving.
KEYRING_SERVICE: Final = "opensre.llm"

# Skip local credential storage entirely. Fail-closed: nothing is written to the
# keyring or to disk, so credentials must come from the process environment.
OPENSRE_DISABLE_KEYRING_ENV: Final = "OPENSRE_DISABLE_KEYRING"

# Opt-in OS keyring writes. Default off: secrets persist to the owner-only
# fallback file (or stay in the process env / ``.env``) so Mac "allow" prompts
# do not block onboarding. Reads still consult the keyring when present.
OPENSRE_USE_KEYRING_ENV: Final = "OPENSRE_USE_KEYRING"

CREDENTIAL_FALLBACK_FILENAME: Final = "credentials.json"

__all__ = [
    "CREDENTIAL_FALLBACK_FILENAME",
    "KEYRING_SERVICE",
    "OPENSRE_DISABLE_KEYRING_ENV",
    "OPENSRE_USE_KEYRING_ENV",
]
