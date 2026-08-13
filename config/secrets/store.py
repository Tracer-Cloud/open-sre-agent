"""The one place that decides where an OpenSRE secret is read from and written to.

Every surface that persists or resolves a credential goes through here, so the
tier order is stated once instead of being re-derived at each call site:

    read   env  ->  owner-only local file
    write  owner-only local file
    delete owner-only local file

The OS keychain is never opened. Reading it raised an approval dialog on macOS
for every secret lookup, and the wizard stripped keys out of ``.env`` on the
assumption the keychain owned them — together that made a working setup ask for
permission on every launch. Secrets written by earlier versions are moved across
once by :mod:`config.secrets.keychain_import`.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass

from config.secrets import keychain_import, local_file, os_keyring
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)


@dataclass(frozen=True)
class SecretLookup:
    """Where a secret resolved from."""

    value: str
    tier: SecretTier


@dataclass(frozen=True)
class SecretSaveResult:
    """Which tier accepted the write."""

    tier: SecretTier
    detail: str = ""

    @property
    def used_fallback(self) -> bool:
        return self.tier == SecretTier.FALLBACK


def lookup(env_var: str, *, default: str = "") -> SecretLookup:
    """Resolve a secret from the environment, then the local file."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return SecretLookup(env_value, SecretTier.ENV)

    # The disable switch takes the machine out of local persistence entirely,
    # so the file is not consulted and env vars are the only source.
    if os_keyring.keyring_is_disabled():
        return SecretLookup("", SecretTier.NONE)

    keychain_import.import_keychain_secrets_once()
    stored_value = local_file.get(env_var)
    if stored_value:
        return SecretLookup(stored_value, SecretTier.FALLBACK)
    return SecretLookup("", SecretTier.NONE)


def resolve_secret(env_var: str, *, default: str = "") -> str:
    """Resolve a secret, or ``""`` when no tier has it."""
    return lookup(env_var, default=default).value


def secret_source(env_var: str) -> SecretTier:
    """Which tier would serve this secret, without exposing its value."""
    return lookup(env_var).tier


def save_secret(env_var: str, value: str) -> SecretSaveResult:
    """Persist a secret to the owner-only local file.

    Raises :class:`KeyringUnavailableError` when the write did not land, so a
    caller that sees no exception knows the credential is durable.
    """
    normalized = value.strip()
    if not normalized:
        delete_secret(env_var)
        return SecretSaveResult(SecretTier.NONE, f"{env_var} cleared.")

    if os_keyring.keyring_is_disabled():
        raise KeyringUnavailableError(
            f"{env_var} not saved: local credential storage is disabled. "
            "Export the secret in the process environment instead.",
            reason=KeyringUnavailableReason.DISABLED,
        )
    try:
        local_file.set(env_var, normalized)
    except OSError as file_exc:
        raise KeyringUnavailableError(
            f"Writing {env_var} to {local_file.store_path()} failed.",
            reason=KeyringUnavailableReason.NO_BACKEND,
        ) from file_exc
    return SecretSaveResult(
        SecretTier.FALLBACK,
        f"{env_var} stored in {local_file.store_path()}.",
    )


def delete_secret(env_var: str) -> None:
    """Remove a stored secret from the local file and any legacy keychain copy.

    Never raises for an absent entry — logout must not fail because what it was
    clearing was already gone. The keychain scrub covers credentials migrated
    from older installs (and any leftover the one-time importer did not drop).

    The scrub runs even under ``OPENSRE_DISABLE_KEYRING``: that switch declines
    local *persistence*, and skipping revocation there left a logged-out
    credential recoverable by unsetting the flag or running an older release.
    """
    with suppress(OSError):
        local_file.delete(env_var)
    with suppress(KeyringUnavailableError, OSError, RuntimeError):
        os_keyring.delete(env_var)


def keyring_is_disabled() -> bool:
    """Whether ``OPENSRE_DISABLE_KEYRING`` takes this machine out of local storage.

    Env vars stay the only source when set; nothing is written to disk.
    """
    return os_keyring.keyring_is_disabled()


__all__ = [
    "SecretLookup",
    "SecretSaveResult",
    "delete_secret",
    "keyring_is_disabled",
    "lookup",
    "resolve_secret",
    "save_secret",
    "secret_source",
]
