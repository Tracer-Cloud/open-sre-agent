"""Shared contracts for the secret storage tiers.

Leaf module: both :mod:`config.secrets.os_keyring` and
:mod:`config.secrets.local_file` import it, and :mod:`config.secrets.store`
imports all three, so the shared error type lives here to keep the package
acyclic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

# Where a resolved secret came from. ``fallback`` is the owner-only local file
# used when the OS keyring cannot be reached.
SecretTier = Literal["env", "keyring", "fallback", "none"]


class KeyringUnavailableReason(StrEnum):
    """Why the OS keyring could not serve a request.

    The distinction drives policy: with ``NO_BACKEND`` there is no secure store
    on this machine to downgrade *from*, so falling back to a local file is the
    only way to reach a working state. With ``BACKEND_ERROR`` a real keychain
    exists and is merely locked or unreachable, so the user is told what
    happened rather than silently handed weaker storage.
    """

    DISABLED = "disabled"
    NO_BACKEND = "no_backend"
    BACKEND_ERROR = "backend_error"


class KeyringUnavailableError(RuntimeError):
    """Raised when the OS keyring cannot store or return a secret.

    Subclasses ``RuntimeError`` because callers predating the secret-store split
    (``_persist_env_secret``, ``persist_api_key_secret``) already treat a
    ``RuntimeError`` from this layer as "storage refused the write".
    """

    def __init__(self, message: str, *, reason: KeyringUnavailableReason) -> None:
        super().__init__(message)
        self.reason = reason


__all__ = [
    "KeyringUnavailableError",
    "KeyringUnavailableReason",
    "SecretTier",
]
