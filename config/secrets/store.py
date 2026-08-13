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
from dataclasses import dataclass

from filelock import Timeout as FileLockTimeout

from config.secrets import local_file, os_keyring
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)
from config.secrets.keychain_import import (
    import_keychain_secrets_once,
    import_named_keychain_secret,
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

    try:
        import_keychain_secrets_once()
        stored_value = local_file.get(env_var)
    except local_file.LOCAL_STORE_ERRORS:
        # Contended credential file — miss this call; do not abort startup.
        return SecretLookup("", SecretTier.NONE)
    if stored_value:
        return SecretLookup(stored_value, SecretTier.FALLBACK)
    # Non-enumerable platforms never write the permanent import marker; a
    # dynamic keychain-only name becomes visible the first time it is looked up.
    try:
        migrated = import_named_keychain_secret(env_var)
    except local_file.LOCAL_STORE_ERRORS:
        return SecretLookup("", SecretTier.NONE)
    if migrated:
        return SecretLookup(migrated, SecretTier.FALLBACK)
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
    except local_file.LOCAL_STORE_ERRORS as file_exc:
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

    Absent entries are fine. A machine with no keychain backend has nothing to
    scrub. Failure to clear either tier raises :class:`KeyringUnavailableError`
    — logout must not report success while a copy remains resolvable (local
    file lock timeout) or recoverable (OS keychain).
    """
    local_error: BaseException | None = None
    try:
        local_file.delete(env_var)
    except (local_file.LocalStoreError, FileLockTimeout, OSError) as exc:
        # Do not suppress: a contended store would leave the credential
        # resolvable after a "successful" logout.
        local_error = exc

    try:
        os_keyring.delete(env_var)
    except KeyringUnavailableError as exc:
        if exc.reason != KeyringUnavailableReason.NO_BACKEND:
            raise KeyringUnavailableError(
                f"Could not delete the OS keychain copy of {env_var}. "
                "Unlock the keychain and retry logout.",
                reason=exc.reason,
            ) from exc
    except (OSError, RuntimeError) as exc:
        raise KeyringUnavailableError(
            f"Could not delete the OS keychain copy of {env_var}. "
            "Unlock the keychain and retry logout.",
            reason=KeyringUnavailableReason.BACKEND_ERROR,
        ) from exc

    if local_error is not None:
        raise KeyringUnavailableError(
            f"Could not remove the local copy of {env_var}. "
            "Retry logout when the credential store is available.",
            reason=KeyringUnavailableReason.BACKEND_ERROR,
        ) from local_error


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
